# SPDX-License-Identifier: GPL-3.0-only
"""Absturz-Behandlung: unbehandelte Ausnahmen (auch aus Threads) zeigen einen freundlichen Dialog
mit Details, „Fehlerbericht speichern“ (ZIP) und „Weiter“. Alles landet auch im Protokoll."""
from __future__ import annotations

import io
import json
import logging
import re
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)
from ..i18n import _

log = logging.getLogger("fm1companion.crash")


# ------------------------------------------------------------ Bericht ---
def _sanitize(obj, home: str):
    """bereinigte Kopie: keine Zugangsdaten (token/passw…/secret/api_key, in jeder Tiefe), keine Notizen,
    keine vollen Seriennummern, keine Fenstergeometrie, Home -> ~, Rechner- und Benutzername ersetzt."""
    from ..redact import HOST_PLACEHOLDER, redact, is_secret_key
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("window_geometry",):
                continue
            if is_secret_key(k):
                out[k] = redact({k: v})[k]
                continue
            if k in ("serial",) and isinstance(v, str) and len(v) > 4:
                v = "…" + v[-4:]
            if k in ("notes",) and v:
                v = "(entfernt)"
            if k in ("Rechnername", "hostname", "host_name") and isinstance(v, str):
                v = HOST_PLACEHOLDER
            out[k] = _sanitize(v, home)
        return out
    if isinstance(obj, (list, tuple)):
        return [_sanitize(x, home) for x in obj]
    if isinstance(obj, str):
        return redact(obj, home=home)
    return obj


def _clean(text: str, home: str) -> str:
    from ..redact import redact_text
    return redact_text(text, home=home)


def build_report(path: Path, exc_text: str = "", extra: dict | None = None) -> Path:
    """ZIP mit Protokollen, bereinigten Einstellungen, Versionen, Ladefehlern und Systeminfos."""
    from .. import applog, config, diag
    from .pages import load_errors
    home = str(Path.home())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        info = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "versions": diag.versions(), **(extra or {})}
        z.writestr("info.json", json.dumps(_sanitize(info, home), indent=2, ensure_ascii=False))
        if exc_text:
            z.writestr("fehler.txt", _clean(exc_text, home))
        z.writestr("seiten-ladefehler.txt", _clean("\n\n".join(f"{m}\n{tb}" for m, tb in load_errors), home)
                   or "keine\n")                      # i18n: ignore (report content)
        deps = [{"paket": s.package, "ok": s.ok, "pflicht": s.required} for s in diag.check_deps()]
        z.writestr("abhaengigkeiten.json", json.dumps(deps, indent=2, ensure_ascii=False))
        try:
            from dataclasses import asdict
            z.writestr("einstellungen.json", json.dumps(_sanitize(asdict(config.load()), home), indent=2,
                                                        ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            z.writestr("einstellungen.json", json.dumps({"fehler": str(e)}))
        try:
            dev = json.loads((config.DATA_DIR / "devices.json").read_text("utf-8"))
            z.writestr("geraete.json", json.dumps(_sanitize(dev, home), indent=2, ensure_ascii=False))
        except (OSError, ValueError):
            pass
        for f in sorted(applog.log_dir().glob("*.log*")) if applog.log_dir().exists() else []:
            try:
                z.writestr(f"logs/{f.name}", _clean(f.read_text("utf-8", errors="replace"), home))
            except OSError:
                pass
        try:
            from .. import health
            mon = health.PiMonitor()
            sysinfo = {"statisch": mon.static(), "usb": mon.usb_devices(), "soundkarten": mon.audio_cards()}
            z.writestr("system.json", json.dumps(_sanitize(sysinfo, home), indent=2, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            pass
        z.writestr("LIESMICH.txt", "Fehlerbericht der FM-1 Companion Suite.\nEnthält Protokolle, Versionen und "  # i18n: ignore
                   "Einstellungen ohne persönliche Pfade, Notizen, Zugangsdaten (Tokens, Passwörter),\n"
                   "Rechnernamen und vollständige Seriennummern.\n"
                   "Keine Backups, keine Samples, keine Projekte.\n")
    return path


def default_report_path() -> Path:
    return Path.home() / f"FM-1-Companion-Fehlerbericht-{time.strftime('%Y-%m-%d_%H-%M-%S')}.zip"


def save_report_dialog(parent, exc_text: str = "", path: str | None = None):
    if not path:
        path, _filter = QFileDialog.getSaveFileName(parent, _("Save bug report"), str(default_report_path()),
                                                    _("ZIP archive (*.zip)"))
    if not path:
        return None
    p = build_report(Path(path), exc_text)
    try:
        from .notify import notify
        notify(_("Bug report saved: {name}").format(name=p.name), "success")
    except Exception:  # noqa: BLE001
        pass
    return p


# ------------------------------------------------------------ Dialog ---
class CrashDialog(QDialog):
    def __init__(self, exc_text: str, parent=None):
        super().__init__(parent)
        self.exc_text = exc_text
        self.saved: Path | None = None
        self.setWindowTitle(_("Unexpected error"))
        self.resize(640, 460)
        lay = QVBoxLayout(self)
        t = QLabel(_("Something went wrong"))
        t.setProperty("role", "pageTitle")
        lay.addWidget(t)
        last = exc_text.strip().splitlines()[-1] if exc_text.strip() else ""
        s = QLabel(_("The app keeps running; your FM-1 was not changed. If the error comes back, save a bug report "
                     "– it contains logs and versions, but no backups or personal paths.")
                   + f"<br><br><b>{last.replace('<', '&lt;')}</b>")
        s.setWordWrap(True)
        lay.addWidget(s)
        self.details = QPlainTextEdit(exc_text)
        self.details.setReadOnly(True)
        self.details.setVisible(False)
        lay.addWidget(self.details, 1)
        row = QHBoxLayout()
        self.b_details = QPushButton(_("Show details"))
        self.b_details.setCheckable(True)
        self.b_details.toggled.connect(lambda on: (self.details.setVisible(on),
                                                   self.b_details.setText(_("Hide details") if on else _("Show details"))))
        self.b_save = QPushButton(_("Save bug report …"))
        self.b_save.clicked.connect(lambda: self.save())
        ok = QPushButton(_("Continue"))
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(self.b_details)
        row.addStretch(1)
        row.addWidget(self.b_save)
        row.addWidget(ok)
        lay.addLayout(row)

    def add(self, exc_text: str):
        self.exc_text += "\n\n" + exc_text
        self.details.setPlainText(self.exc_text)

    def save(self, path: str | None = None):
        p = save_report_dialog(self, self.exc_text, path)
        if p:
            self.saved = p
            self.b_save.setText(_("Saved ✓"))
        return p


class _Relay(QObject):
    crashed = pyqtSignal(str)


class CrashHandler:
    """installiert sys.excepthook und threading.excepthook."""

    def __init__(self, parent_fn=lambda: None):
        self.parent_fn = parent_fn
        self.dialog: CrashDialog | None = None
        self.relay = _Relay()
        self.relay.crashed.connect(self._show)
        self.count = 0
        self._prev = (sys.excepthook, getattr(threading, "excepthook", None))

    def install(self):
        sys.excepthook = self._hook
        threading.excepthook = lambda a: self._hook(a.exc_type, a.exc_value, a.exc_traceback)
        return self

    def uninstall(self):
        sys.excepthook, prev_t = self._prev
        if prev_t:
            threading.excepthook = prev_t

    def _hook(self, t, v, tb):
        if t is KeyboardInterrupt:
            return
        text = "".join(traceback.format_exception(t, v, tb))
        log.error("Unbehandelte Ausnahme:\n%s", text)
        self.count += 1
        self.relay.crashed.emit(text)          # aus Threads: wird im GUI-Thread angezeigt

    def _show(self, text: str):
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.add(text)
            return
        self.dialog = CrashDialog(text, self.parent_fn())
        self.dialog.open()


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", s)


__all__ = ["CrashHandler", "CrashDialog", "build_report", "save_report_dialog", "io"]
