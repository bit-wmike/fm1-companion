#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Graphical installer of the FM-1 Companion Suite (PyQt6 QWizard). Started by the .run package.

  gui.py --source DIR                 interaktiv
  gui.py --source DIR --unattended [--prefix P] [--system] [--desktop] [--demo-data] [--no-menu]
                                    [--no-fluidsynth-fix]

English, German when the system language is German (``core.language()``, ``core.tr()``). The risk notice is
the app's own text (fm1companion/risk.py) and its German translation from fm1companion/locale/de/*.po, read
from the source folder without importing the app.
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
from core import tr  # noqa: E402


def unattended(args) -> int:
    prefix = Path(args.prefix).expanduser() if args.prefix else (core.SYSTEM_PREFIX if args.system else core.user_prefix())
    print(risk_title() + "\n\n" + "\n\n".join(risk_lines(Path(args.source))) + "\n")
    core.install(Path(args.source), prefix, args.system, not args.no_menu, args.desktop, args.demo_data, args.python,
                 fluidsynth_fix=not args.no_fluidsynth_fix)
    return 0


def risk_title() -> str:
    return tr("Important notice", "Wichtiger Hinweis")


def understood_text() -> str:
    return tr("I understand", "Ich habe verstanden")


# fallback in case fm1companion/risk.py is missing in the source folder – the same text as in the app
RISK_FALLBACK_EN = [
    "FM-1 Companion Suite is an unofficial community project and not affiliated with M-VAVE.",
    "Installing firmware, recovery (update mode, ROM boot mode), cleaning up leftovers and writing back from the "
    "Studio change your device. You do this at your own risk.",
    "Make a backup first and do not disconnect the device during an operation.",
    "Not every function has been tested on real hardware (see docs/UNTESTED.md).",
    "There is no warranty and no liability, to the extent permitted by law (GNU GPL 3.0, sections 15 and 16).",
]
RISK_FALLBACK = [
    "Die FM-1 Companion Suite ist ein inoffizielles Community-Projekt und nicht mit M-VAVE verbunden.",
    "Firmware installieren, Rettung (Update-Modus, ROM-Bootmodus), Reste aufräumen und das Zurückschreiben aus dem "
    "Studio verändern dein Gerät. Das geschieht auf eigenes Risiko.",
    "Mach vorher ein Backup und trenne das Gerät während eines Vorgangs nicht.",
    "Nicht jede Funktion ist auf echter Hardware getestet (siehe docs/UNTESTED.md).",
    "Es gibt keine Gewährleistung und keine Haftung, soweit gesetzlich zulässig (GNU GPL 3.0, Abschnitte 15 und 16).",
]


def _risk_source_lines(src: Path) -> list[str] | None:
    """the English TEXT_LINES of fm1companion/risk.py in the source folder (parsed, the app is not imported)"""
    import ast

    def strings(node):                       # "…" or _("…") (adjacent literals are already joined by the parser)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("_", "N_") and len(node.args) == 1:
            node = node.args[0]
        v = ast.literal_eval(node)
        if not isinstance(v, str):
            raise ValueError(v)
        return v
    try:
        tree = ast.parse((Path(src) / "fm1companion" / "risk.py").read_text("utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "TEXT_LINES" for t in node.targets) \
                    and isinstance(node.value, ast.List):
                return [strings(x) for x in node.value.elts] or None
    except (OSError, SyntaxError, ValueError):
        pass
    return None


def _german_catalog(src: Path):
    """the app's German catalog (fm1companion/i18n.py + locale/de/*.po of the source folder), loaded standalone:
    i18n.py only needs the standard library, the fm1companion package itself is not imported."""
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("_fm1c_installer_i18n", Path(src) / "fm1companion" / "i18n.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod             # dataclasses look their module up there
        try:
            spec.loader.exec_module(mod)
            return mod.Catalog("de")
        finally:
            sys.modules.pop(spec.name, None)
    except Exception:  # noqa: BLE001 – the installer must work without it (local fallback below)
        return None


def risk_lines(src: Path, lang: str | None = None) -> list[str]:
    """the risk notice of the app in the installer language (English source text, German from the catalog)."""
    lang = lang or core.language()
    lines = _risk_source_lines(src)
    if lang != "de":
        return lines or list(RISK_FALLBACK_EN)
    cat = _german_catalog(src) if lines else None
    de = [cat.get(None, x) for x in lines] if cat else []
    return de if de and all(de) else list(RISK_FALLBACK)


def interactive(args) -> int:
    from PyQt6.QtCore import QProcess, Qt
    from PyQt6.QtGui import QFont, QIcon
    from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QLabel, QPlainTextEdit, QProgressBar,
                                 QRadioButton, QTextBrowser, QVBoxLayout, QWizard, QWizardPage)
    app = QApplication(sys.argv)
    app.setApplicationName(tr("FM-1 Companion Suite – Setup", "FM-1 Companion Suite – Installation"))
    src = Path(args.source).resolve()
    version = core.read_version(src)
    sys.path.insert(0, str(src))
    try:                                        # same look as the app
        from fm1companion.ui import theme
        theme.apply(app, "auto")
    except Exception:  # noqa: BLE001
        pass
    png = src / "data" / "icons" / "hicolor" / "128x128" / "apps" / "fm1-companion.png"
    app.setWindowIcon(QIcon(str(png)))
    wz = QWizard()
    wz.setWindowTitle(tr(f"Install FM-1 Companion Suite {version}", f"FM-1 Companion Suite {version} installieren"))
    wz.setWizardStyle(QWizard.WizardStyle.ModernStyle)
    wz.setPixmap(QWizard.WizardPixmap.LogoPixmap, QIcon(str(png)).pixmap(56, 56))
    wz.resize(700, 520)
    for b, t in ((QWizard.WizardButton.NextButton, tr("Next >", "Weiter >")),
                 (QWizard.WizardButton.BackButton, tr("< Back", "< Zurück")),
                 (QWizard.WizardButton.FinishButton, tr("Finish", "Fertig")),
                 (QWizard.WizardButton.CancelButton, tr("Cancel", "Abbrechen")),
                 (QWizard.WizardButton.CommitButton, tr("Install", "Installieren"))):
        wz.setButtonText(b, t)

    def label(text):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.RichText)
        return lab

    # welcome
    p1 = QWizardPage()
    p1.setTitle(tr(f"Welcome to FM-1 Companion Suite {version}", f"Willkommen zur FM-1 Companion Suite {version}"))
    p1.setSubTitle(tr("Everything for the M-VAVE FM-1 in one app.", "Alles fürs M-VAVE FM-1 in einer App."))
    v = QVBoxLayout(p1)
    old_user = core.installed_info(core.user_prefix(), False)
    old_sys = core.installed_info(core.SYSTEM_PREFIX, True)
    old = old_user or old_sys
    if old:
        mode = tr("system-wide", "systemweit") if old.get("system") else tr("just for me", "nur für mich")
    v.addWidget(label(tr("Switching firmware, backups, sounds and presets, samples, MIDI, simulator, recording and help – "
                         "this wizard sets everything up.<br><br>",
                         "Firmware wechseln, Backups, Klänge und Presets, Samples, MIDI, Simulator, Aufnahme und Hilfe – "
                         "dieser Assistent richtet alles ein.<br><br>") +
                      (tr(f"<b>Found: version {old['version']} ({mode}).</b> It will be updated; your "
                          "settings and your folder ~/FM-1 are kept.",
                          f"<b>Gefunden: Version {old['version']} ({mode}).</b> Sie wird aktualisiert; deine "
                          "Einstellungen und dein Ordner ~/FM-1 bleiben erhalten.") if old else "")))
    v.addStretch(1)
    wz.addPage(p1)

    # license
    p2 = QWizardPage()
    p2.setTitle(tr("License", "Lizenz"))
    p2.setSubTitle(tr("The suite is free software under the GNU GPL 3.0.", "Die Suite ist freie Software unter der GNU GPL 3.0."))
    v = QVBoxLayout(p2)
    lic = QTextBrowser()
    lic.setPlainText((src / "LICENSE").read_text("utf-8", errors="replace") if (src / "LICENSE").exists() else "GPL-3.0")
    v.addWidget(lic, 1)
    accept = QCheckBox(tr("I accept the license terms", "Ich akzeptiere die Lizenzbedingungen"))
    v.addWidget(accept)
    p2.registerField("accept*", accept)
    wz.addPage(p2)

    # important notice (the same text as before the first installation/recovery in the app)
    p2b = QWizardPage()
    p2b.setTitle(risk_title())
    p2b.setSubTitle(tr("Unofficial, at your own risk, without warranty.", "Inoffiziell, auf eigenes Risiko, ohne Gewährleistung."))
    v = QVBoxLayout(p2b)
    v.addWidget(label("".join(f"<p>{html.escape(t)}</p>" for t in risk_lines(src))))
    understood = QCheckBox(understood_text())
    v.addWidget(understood)
    p2b.registerField("understood*", understood)
    v.addStretch(1)
    wz.addPage(p2b)

    # installation type
    p3 = QWizardPage()
    p3.setTitle(tr("Installation type", "Installationsart"))
    v = QVBoxLayout(p3)
    grp = QButtonGroup(p3)
    user_app = core.layout(core.user_prefix(), False)['app']
    r_user = QRadioButton(tr(f"Just for me  ({user_app}, no password needed)",
                             f"Nur für mich  ({user_app}, kein Passwort nötig)"))
    r_sys = QRadioButton(tr("For all users  (/usr/local, asks for the administrator password)",
                            "Für alle Benutzer  (/usr/local, fragt nach dem Administrator-Passwort)"))
    for r in (r_user, r_sys):
        grp.addButton(r)
        v.addWidget(r)
    (r_sys if (old_sys and not old_user) else r_user).setChecked(True)
    if not shutil.which("pkexec") and os.geteuid() != 0:
        r_sys.setEnabled(False)
        r_sys.setText(r_sys.text() + tr(" – pkexec is missing", " – pkexec fehlt"))
    v.addStretch(1)
    wz.addPage(p3)

    # components
    p4 = QWizardPage()
    p4.setTitle(tr("Components", "Komponenten"))
    v = QVBoxLayout(p4)
    c_menu = QCheckBox(tr("Start menu entry (Multimedia) and “Uninstall FM-1 Companion”",
                          "Eintrag im Startmenü (Multimedia) und „FM-1 Companion deinstallieren“"))
    c_menu.setChecked(True)
    c_desk = QCheckBox(tr("Shortcut on the desktop", "Verknüpfung auf dem Schreibtisch"))
    c_demo = QCheckBox(tr("Example samples (in ~/FM-1/Samples/Beispiele (Demo))",
                          "Beispiel-Samples (in ~/FM-1/Samples/Beispiele (Demo))"))
    c_fluid = QCheckBox(tr("Disable the FluidSynth system service (fluidsynth.service grabs the HDMI sound card at "
                           "every login; FluidSynth stays installed, the app starts it itself)",
                           "FluidSynth-Systemdienst abschalten (fluidsynth.service belegt bei jeder Anmeldung die "
                           "HDMI-Soundkarte; FluidSynth bleibt installiert, die App startet es selbst)"))
    c_fluid.setChecked(True)
    c_fluid.setVisible(core.fluidsynth_service_enabled())   # only ask if the service is really active
    for c in (c_menu, c_desk, c_demo, c_fluid):
        v.addWidget(c)
    v.addStretch(1)
    wz.addPage(p4)

    # progress
    p5 = QWizardPage()
    p5.setTitle("Installation")                  # same word in English and German
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

    # done
    p6 = QWizardPage()
    p6.setTitle(tr("Done", "Fertig"))
    v = QVBoxLayout(p6)
    done_lab = label("")
    v.addWidget(done_lab)
    c_start = QCheckBox(tr("Start FM-1 Companion now", "FM-1 Companion jetzt starten"))
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
            out.appendPlainText(tr("Please enter the administrator password …", "Bitte das Administrator-Passwort eingeben …"))
            cmd = ["pkexec"] + cmd
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: [out.appendPlainText(x) for x in
                                                      bytes(proc.readAllStandardOutput()).decode(errors="replace").splitlines()]
                                             or bar.setValue(min(7, bar.value() + 1)))

        def finished(code, _st):
            state["done"], state["ok"] = True, code == 0
            bar.setValue(7 if code == 0 else 0)
            if code != 0:
                out.appendPlainText(tr(f"\nERROR: installation aborted (code {code}).",
                                       f"\nFEHLER: Installation abgebrochen (Code {code})."))
            p5.completeChanged.emit()
        proc.finished.connect(finished)
        proc.start(cmd[0], cmd[1:])

    def page_changed(i):
        if wz.page(i) is p5 and not state["done"]:
            run_install()
        if wz.page(i) is p6:
            done_lab.setText(tr("<b>The FM-1 Companion Suite is installed.</b><br>You find it in the start menu under "
                                "Multimedia. Try it without a device: <code>fm1-companion --demo</code>",
                                "<b>Die FM-1 Companion Suite ist installiert.</b><br>Du findest sie im Startmenü unter "
                                "Multimedia. Ohne Gerät ausprobieren: <code>fm1-companion --demo</code>")
                             if state["ok"] else tr("The installation did not work – details on the previous page.",
                                                    "Die Installation hat nicht geklappt – Details auf der vorigen Seite."))
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
