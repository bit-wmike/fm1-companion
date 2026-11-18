# SPDX-License-Identifier: GPL-3.0-only
"""Crash handling: unhandled exceptions (including from threads) show a friendly dialog
with details, “Save error report” (ZIP) and “Continue”. Everything also ends up in the log."""
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

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)
from ..i18n import N_, _

log = logging.getLogger("fm1companion.crash")


ISSUES_URL = "https://github.com/bit-wmike/fm1-companion/issues"
# what is said wherever a report is offered or saved (dialog, notification, help) – keep it honest
PRIVACY_NOTE = N_("Removed automatically: credentials (tokens, passwords), your home folder, user and computer "
                  "name, device names (replaced by “Device 1” …), notes, serial numbers except the last four "
                  "characters, hardware addresses (MAC) and Wi-Fi names. It may still contain file names and "
                  "other details – please look through it before you post it publicly.")
README_TEXT = """Bug report of the FM-1 Companion Suite
=====================================

Where to send it: {issues}
(open a new issue there, describe what you did, and attach this ZIP file)

What is in here: the app's logs, the last firmware installation logs, versions, the list of
installed packages, your settings and the list of your FM-1 devices, some system information
(USB devices, sound cards).
No backups, no samples, no projects, no firmware packages.

Removed or replaced automatically:
  * credentials: tokens, passwords, API keys, user:password in addresses, the part after "?" in
    web addresses
  * your home folder (-> ~), your user name (-> <benutzer>) and the computer name (-> <rechner>),
    folder names below /home, /media and /run/media
  * a main folder or sample folder outside your home folder (-> <path>)
  * the names of your FM-1 devices (-> "Device 1", "Device 2" ... the same number everywhere in
    this report)
  * device notes, window positions
  * serial numbers: only the last four characters stay
  * hardware addresses (MAC/Bluetooth -> <mac>) and Wi-Fi names that follow the word "SSID"
    (-> <netz>)

What this cannot do: the removal works with patterns, it does not understand the text.
The report may still contain file names and folder names (for example of projects, samples and
backups), names of USB devices and sound cards, network addresses (IP), and anything personal that
happens to appear in a log line in a form the patterns do not know.

Please look through the files before you post the report publicly. They are plain text; you may
delete lines or whole files from the ZIP - say so in the issue.
"""


# ------------------------------------------------------------ Report ---
def _sanitize(obj, home: str, extra: dict | None = None, names: dict | None = None):
    """sanitized copy: no credentials (token/passw…/secret/api_key, at any depth), no notes,
    no full serial numbers (every key that contains "serial"), no window geometry, home -> ~, host and user name
    replaced, texts from ``extra`` replaced, device names (key "name", if ``names`` is given) -> "Device N"."""
    from ..redact import HOST_PLACEHOLDER, redact, is_secret_key, is_serial_key, mask_serial
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("window_geometry",):
                continue
            if is_secret_key(k):
                out[k] = redact({k: v})[k]
                continue
            if is_serial_key(k) and isinstance(v, (str, int)) and not isinstance(v, bool):
                out[k] = mask_serial(str(v))
                continue
            if k in ("notes",) and v:
                v = "(entfernt)"
            if k in ("Rechnername", "hostname", "host_name") and isinstance(v, str):
                v = HOST_PLACEHOLDER
            if names is not None and k == "name" and isinstance(v, str) and v in names:
                out[k] = names[v]
                continue
            out[k] = _sanitize(v, home, extra, names)
        return out
    if isinstance(obj, (list, tuple)):
        return [_sanitize(x, home, extra, names) for x in obj]
    if isinstance(obj, str):
        return redact(obj, home=home, extra=extra)
    return obj


def _clean(text: str, home: str, extra: dict | None = None) -> str:
    from ..redact import redact_text
    return redact_text(text, home=home, extra=extra)


_GENERIC_NAMES = {"fm-1", "fm1", "m-vave fm-1", "mvave fm-1", "fm-1 (midi only)"}


def device_names(devices) -> dict:
    """{device name: "Device N"} for the profiles of devices.json – stable inside one report."""
    out: dict = {}
    profiles = devices.get("profiles", []) if isinstance(devices, dict) else []
    for p in profiles if isinstance(profiles, list) else []:
        name = p.get("name") if isinstance(p, dict) else None
        if isinstance(name, str) and name and name not in out:
            out[name] = f"Device {len(out) + 1}"
    return out


def private_literals(settings: dict, names: dict, home: str) -> dict:
    """Texts only this installation knows, with their placeholders, for every file of the report:
    device names (and the folder names made from them) and main/sample folders outside the home folder.
    Very short or generic device names ("FM-1") are not replaced in running text – that would garble the logs;
    in the device list itself they are still shown as "Device N"."""
    from ..redact import PATH_PLACEHOLDER
    extra: dict = {}
    for name, label in names.items():
        if len(name) < 3 or name.strip().lower() in _GENERIC_NAMES:
            continue
        extra[name] = label
        try:
            from ..devices import safe_folder_name
            folder = safe_folder_name(name, "")
        except Exception:  # noqa: BLE001
            folder = ""
        if folder and len(folder) >= 3 and folder.lower() not in _GENERIC_NAMES:
            extra.setdefault(folder, label)
    roots = [settings.get("user_root")] + list(settings.get("sample_roots") or [])
    for r in roots:
        if not isinstance(r, str):
            continue
        r = r.rstrip("/")
        inside = bool(home) and (r == home or r.startswith(home.rstrip("/") + "/"))
        if len(r) >= 4 and r.startswith("/") and not inside and not r.startswith("~"):
            extra[r] = PATH_PLACEHOLDER
    return extra


def build_report(path: Path, exc_text: str = "", extra: dict | None = None) -> Path:
    """ZIP with logs, sanitized settings, versions, load errors and system info."""
    from .. import applog, config, diag
    from .pages import load_errors
    home = str(Path.home())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from dataclasses import asdict
        settings, settings_error = asdict(config.load()), ""
    except Exception as e:  # noqa: BLE001
        settings, settings_error = {}, str(e)
    try:
        dev = json.loads((config.DATA_DIR / "devices.json").read_text("utf-8"))
    except (OSError, ValueError):
        dev = None
    names = device_names(dev)
    lit = private_literals(settings, names, home)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        info = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "versions": diag.versions(), **(extra or {})}
        z.writestr("info.json", json.dumps(_sanitize(info, home, lit), indent=2, ensure_ascii=False))
        if exc_text:
            z.writestr("fehler.txt", _clean(exc_text, home, lit))
        z.writestr("seiten-ladefehler.txt", _clean("\n\n".join(f"{m}\n{tb}" for m, tb in load_errors), home, lit)
                   or "keine\n")                      # i18n: ignore (report content)
        deps = [{"paket": s.package, "ok": s.ok, "pflicht": s.required} for s in diag.check_deps()]
        z.writestr("abhaengigkeiten.json", json.dumps(deps, indent=2, ensure_ascii=False))
        if settings_error:
            z.writestr("einstellungen.json", json.dumps({"fehler": _clean(settings_error, home, lit)}))
        else:
            z.writestr("einstellungen.json", json.dumps(_sanitize(settings, home, lit), indent=2,
                                                        ensure_ascii=False))
        if dev is not None:
            z.writestr("geraete.json", json.dumps(_sanitize(dev, home, lit, names), indent=2, ensure_ascii=False))
        for f in sorted(applog.log_dir().glob("*.log*")) if applog.log_dir().exists() else []:
            try:
                z.writestr(f"logs/{f.name}", _clean(f.read_text("utf-8", errors="replace"), home, lit))
            except OSError:
                pass
        try:                                   # firmware installations: flash logs and the journal (no packages)
            from ..flash import journal as fj, robust
            for f in robust.latest_logs(fj.root() / "logs", 3):
                z.writestr(f"flash/{f.name}", _clean(f.read_text("utf-8", errors="replace"), home, lit))
            jd = fj.Journal().load()
            jd["history"] = jd.get("history", [])[-10:]
            z.writestr("flash/journal.json", json.dumps(_sanitize(jd, home, lit), indent=2, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
        try:
            from .. import health
            mon = health.PiMonitor()
            sysinfo = {"statisch": mon.static(), "usb": mon.usb_devices(), "soundkarten": mon.audio_cards()}
            z.writestr("system.json", json.dumps(_sanitize(sysinfo, home, lit), indent=2, ensure_ascii=False,
                                                 default=str))
        except Exception:  # noqa: BLE001
            pass
        z.writestr("README.txt", README_TEXT.format(issues=ISSUES_URL))
    return path


def default_report_name() -> str:
    # Translators: start of the file name of a saved bug report – only letters, digits and hyphens, no spaces
    stem = "".join(c for c in _("FM-1-Companion-bug-report") if c.isalnum() or c in "-_") or "FM-1-Companion-bug-report"
    return f"{stem}-{time.strftime('%Y-%m-%d_%H-%M-%S')}.zip"


def default_report_path() -> Path:
    return Path.home() / default_report_name()


def open_issues_page() -> bool:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    return QDesktopServices.openUrl(QUrl(ISSUES_URL))


def show_in_folder(path: Path) -> bool:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


class ReportSavedDialog(QDialog):
    """After saving: where the report is, where to send it, and the reminder to look through it first."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(_("Bug report saved"))
        lay = QVBoxLayout(self)
        t = QLabel(_("Bug report saved"))
        t.setProperty("role", "pageTitle")
        lay.addWidget(t)
        where = QLabel(str(self.path))
        where.setTextFormat(Qt.TextFormat.PlainText)
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        where.setWordWrap(True)
        lay.addWidget(where)
        how = QLabel(_("To send it: open the project's issues page on GitHub ({url}), create a new issue, "
                       "describe what you did and attach this file. A (free) GitHub account is needed. "
                       "Nothing is sent automatically.").format(url=ISSUES_URL))
        how.setTextFormat(Qt.TextFormat.PlainText)
        how.setWordWrap(True)
        lay.addWidget(how)
        note = QLabel(_(PRIVACY_NOTE))
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        self.b_issues = QPushButton(_("Open the issues page"))
        self.b_issues.clicked.connect(lambda: open_issues_page())
        self.b_folder = QPushButton(_("Show file in folder"))
        self.b_folder.clicked.connect(lambda: show_in_folder(self.path))
        close = QPushButton(_("Close"))
        close.setProperty("variant", "primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(self.b_issues)
        row.addWidget(self.b_folder)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.resize(560, self.sizeHint().height())


_saved_dialogs: list = []           # keeps the (non-modal) dialogs alive


def save_report_dialog(parent, exc_text: str = "", path: str | None = None, show_next: bool = True):
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
    if show_next:
        try:
            dlg = ReportSavedDialog(p, parent)
            _saved_dialogs[:] = [d for d in _saved_dialogs[-3:] if d.isVisible()] + [dlg]
            dlg.open()                                 # not exec(): never blocks the caller
        except Exception:  # noqa: BLE001 – the report itself is saved
            log.exception("could not show the “report saved” dialog")
    return p


# ------------------------------------------------------------ Device state at the time of the error ---
_DEVICE_IO_MARKS = ("/flash/", "transport.py", "fm1open", "x0xbk", "backup", "restore", "send_raw", "send_midi",
                    "send_sysex", "bridge.py", "/vendor/unbricker/")


def device_write_possible(exc_text: str = "", window=None) -> bool:
    """Could the FM-1 have been in the middle of an operation when the error happened?
    True if (a) a firmware installation is running, (b) the app holds the device for one of its own operations
    (link.hold()/exclusive(), used by backup, restore, upload …) or (c) the traceback passes through the code
    that talks to the device. False only means: the app found no sign of it."""
    try:
        from .. import storage
        if storage.install_running():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        link = getattr(getattr(window, "ctx", None), "link", None)
        if link is not None and int(getattr(link, "_exclusive_depth", 0) or 0) > 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    frames = "\n".join(l for l in (exc_text or "").splitlines() if l.lstrip().startswith("File "))
    return any(m in frames for m in _DEVICE_IO_MARKS)


# ------------------------------------------------------------ Dialog ---
class CrashDialog(QDialog):
    def __init__(self, exc_text: str, parent=None, device_unknown: bool | None = None):
        super().__init__(parent)
        self.exc_text = exc_text
        self.saved: Path | None = None
        self.device_unknown = device_write_possible(exc_text, parent) if device_unknown is None else device_unknown
        self.setWindowTitle(_("Unexpected error"))
        self.resize(640, 480)
        lay = QVBoxLayout(self)
        t = QLabel(_("Something went wrong"))
        t.setProperty("role", "pageTitle")
        lay.addWidget(t)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self._show_summary()
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

    def _show_summary(self):
        import html
        last = self.exc_text.strip().splitlines()[-1] if self.exc_text.strip() else ""
        if self.device_unknown:
            state = _("The app keeps running. The error happened while the app was working with the FM-1 – it "
                      "is not known whether the FM-1 was changed. Check the device; if it does not start or "
                      "behaves oddly, see the page “Recovery”.")
        else:
            state = _("The app keeps running. As far as the app can tell, it was not writing to your FM-1 at "
                      "that moment.")
        report = _("If the error comes back, save a bug report and attach it to a new issue on the project's "
                   "GitHub issues page ({url}). The report contains logs, versions and settings, no backups, "
                   "samples or projects. Personal paths, names and credentials are removed as far as the app "
                   "can recognise them – please look through it before you post it publicly.").format(url=ISSUES_URL)
        self.summary.setText(f"{html.escape(state)}<br><br>{html.escape(report)}<br><br><b>{html.escape(last)}</b>")

    def add(self, exc_text: str, device_unknown: bool = False):
        self.exc_text += "\n\n" + exc_text
        self.details.setPlainText(self.exc_text)
        if device_unknown and not self.device_unknown:
            self.device_unknown = True
        self._show_summary()

    def save(self, path: str | None = None):
        p = save_report_dialog(self, self.exc_text, path)
        if p:
            self.saved = p
            self.b_save.setText(_("Saved ✓"))
        return p


class _Relay(QObject):
    crashed = pyqtSignal(str, bool)


class CrashHandler:
    """installs sys.excepthook and threading.excepthook."""

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
        try:                                   # now, in the failing thread – later the job has let go of the device
            unknown = device_write_possible(text, self.parent_fn())
        except Exception:  # noqa: BLE001
            unknown = True
        self.relay.crashed.emit(text, unknown)  # from threads: shown in the GUI thread

    def _show(self, text: str, device_unknown: bool = False):
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.add(text, device_unknown)
            return
        self.dialog = CrashDialog(text, self.parent_fn(), device_unknown=device_unknown)
        self.dialog.open()


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", s)


__all__ = ["CrashHandler", "CrashDialog", "ReportSavedDialog", "build_report", "save_report_dialog",
           "device_write_possible", "ISSUES_URL", "io"]
