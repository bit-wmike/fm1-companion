#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
""""Uninstall FM-1 Companion": dialog (or --unattended) – removes the app and, if wanted, settings and internal
data. The main folder (~/FM-1) is NEVER deleted. English, German when the system language is German (core.tr)."""
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
from core import tr  # noqa: E402


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
    d.setWindowTitle(tr("Uninstall FM-1 Companion", "FM-1 Companion deinstallieren"))
    lay = QVBoxLayout(d)
    info = core.installed_info(prefix, a.system) or {}
    ver, root = info.get('version', ''), core._user_root()
    lab = QLabel(tr(f"<b>Remove FM-1 Companion Suite {ver}?</b><br><br>"
                    f"Your folder <b>{root}</b> with backups, projects, samples and recordings is "
                    "<b>always</b> kept.",
                    f"<b>FM-1 Companion Suite {ver} entfernen?</b><br><br>"
                    f"Dein Ordner <b>{root}</b> mit Backups, Projekten, Samples und Aufnahmen bleibt "
                    "<b>immer</b> erhalten."))
    lab.setWordWrap(True)
    lay.addWidget(lab)
    c_set = QCheckBox(tr("also remove settings and device profiles", "auch Einstellungen und Geräteprofile entfernen"))
    c_dat = QCheckBox(tr("also remove internal data (firmware tools, editor copies, cache, logs)",
                         "auch interne Daten entfernen (Firmware-Werkzeuge, Editor-Kopien, Zwischenspeicher, Protokolle)"))
    lay.addWidget(c_set)
    lay.addWidget(c_dat)
    bb = QDialogButtonBox()
    bb.addButton(tr("Uninstall", "Deinstallieren"), QDialogButtonBox.ButtonRole.AcceptRole)
    bb.addButton(tr("Cancel", "Abbrechen"), QDialogButtonBox.ButtonRole.RejectRole)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    lay.addWidget(bb)
    if not d.exec():
        return 1
    from PyQt6.QtWidgets import QMessageBox
    if a.system and os.geteuid() != 0 and shutil.which("pkexec"):
        r = subprocess.run(["pkexec", sys.executable, str(HERE / "core.py"), "uninstall", "--system", "--prefix", str(prefix)])
        if r.returncode != 0:      # password prompt cancelled or error: do not report anything as "removed"
            QMessageBox.warning(None, tr("Not removed", "Nicht entfernt"),
                                tr(f"Uninstalling was cancelled (code {r.returncode}).",
                                   f"Die Deinstallation wurde abgebrochen (Code {r.returncode})."))
            return 1
        core.uninstall(Path("/nonexistent"), False, c_set.isChecked(), c_dat.isChecked())   # user data without root
    else:
        core.uninstall(prefix, a.system, c_set.isChecked(), c_dat.isChecked())
    QMessageBox.information(None, tr("Done", "Fertig"), tr("FM-1 Companion has been removed.", "FM-1 Companion wurde entfernt."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
