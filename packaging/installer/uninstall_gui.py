#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""„FM-1 Companion deinstallieren“: Dialog (oder --unattended) – entfernt die App, auf Wunsch Einstellungen und
interne Daten. Der Hauptordner (~/FM-1) wird NIE gelöscht."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="")
    ap.add_argument("--system", action="store_true")
    ap.add_argument("--unattended", action="store_true")
    ap.add_argument("--settings", action="store_true")
    ap.add_argument("--data", action="store_true")
    a = ap.parse_args(argv)
    prefix = Path(a.prefix).expanduser() if a.prefix else (core.SYSTEM_PREFIX if a.system else core.user_prefix())
    if a.unattended:
        core.uninstall(prefix, a.system, a.settings, a.data)
        return 0
    from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout
    app = QApplication(sys.argv)
    d = QDialog()
    d.setWindowTitle("FM-1 Companion deinstallieren")
    lay = QVBoxLayout(d)
    info = core.installed_info(prefix, a.system) or {}
    lab = QLabel(f"<b>FM-1 Companion Suite {info.get('version', '')} entfernen?</b><br><br>"
                 f"Dein Ordner <b>{core._user_root()}</b> mit Backups, Projekten, Samples und Aufnahmen bleibt "
                 "<b>immer</b> erhalten.")
    lab.setWordWrap(True)
    lay.addWidget(lab)
    c_set = QCheckBox("auch Einstellungen und Geräteprofile entfernen")
    c_dat = QCheckBox("auch interne Daten entfernen (Firmware-Werkzeuge, Editor-Kopien, Zwischenspeicher, Protokolle)")
    lay.addWidget(c_set)
    lay.addWidget(c_dat)
    bb = QDialogButtonBox()
    bb.addButton("Deinstallieren", QDialogButtonBox.ButtonRole.AcceptRole)
    bb.addButton("Abbrechen", QDialogButtonBox.ButtonRole.RejectRole)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    lay.addWidget(bb)
    if not d.exec():
        return 1
    from PyQt6.QtWidgets import QMessageBox
    if a.system and os.geteuid() != 0 and shutil.which("pkexec"):
        r = subprocess.run(["pkexec", sys.executable, str(HERE / "core.py"), "uninstall", "--system", "--prefix", str(prefix)])
        if r.returncode != 0:      # Passwortabfrage abgebrochen oder Fehler: nichts als „entfernt“ melden
            QMessageBox.warning(None, "Nicht entfernt", f"Die Deinstallation wurde abgebrochen (Code {r.returncode}).")
            return 1
        core.uninstall(Path("/nonexistent"), False, c_set.isChecked(), c_dat.isChecked())   # Benutzerdaten ohne root
    else:
        core.uninstall(prefix, a.system, c_set.isChecked(), c_dat.isChecked())
    QMessageBox.information(None, "Fertig", "FM-1 Companion wurde entfernt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
