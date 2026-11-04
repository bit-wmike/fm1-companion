# SPDX-License-Identifier: GPL-3.0-only
"""Schmale Rechner-Anzeige in der Statusleiste (CPU, RAM, Temperatur, freier Platz) und der Systemcheck.

Bewusst klein: die App dreht sich ums FM-1. Ein Klick öffnet den Systemcheck, der im Kernel-Protokoll
nach Problemen mit USB, Strom und Audio sucht und sie auf Deutsch erklärt.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QToolButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import health, syscheck
from .common import fmt_secs, fmt_size, heading, icon, run_job
from ..i18n import _, decimal, ngettext

LEVEL_ICON = {"ok": "emblem-ok", "info": "dialog-information", "warn": "dialog-warning", "error": "dialog-error",
              "unknown": "help-browser"}
LEVEL_TEXT = {"ok": _("OK"), "info": _("Note"), "warn": _("Warning"), "error": _("Problem"), "unknown": _("unknown")}


def _de(v: float, digits=0) -> str:
    return decimal(v, digits)


class _Sig(QObject):
    throttled = pyqtSignal(object, object)


class PiStatus(QWidget):
    """CPU · RAM · °C · frei – alle 3 s aus /proc und /sys; get_throttled alle 30 s im Hintergrund."""
    INTERVAL_MS = 3000
    THROTTLE_EVERY_S = 30

    def __init__(self, folder_fn, open_check, parent=None, monitor=None):
        super().__init__(parent)
        self.setObjectName("PiStatus")
        self.monitor = monitor or health.PiMonitor()
        self.folder_fn = folder_fn
        self.values: dict = {}
        self.thr_bits: int | None = None
        self.thr_names: list[str] = []
        self._thr_at = 0.0
        self._sig = _Sig()
        self._sig.throttled.connect(self._got_throttled)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 4, 0)
        h.setSpacing(0)
        self.button = QToolButton()
        self.button.setObjectName("PiStatusButton")
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setAutoRaise(True)
        self.button.clicked.connect(open_check)
        self.button.setAccessibleName(_("Computer status, click opens the system check"))
        h.addWidget(self.button)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(self.INTERVAL_MS)
        QTimer.singleShot(0, self.refresh)

    def stop(self):
        self.timer.stop()

    @property
    def warning(self) -> bool:
        return bool(self.values.get("undervoltage")) or bool(self.thr_bits and self.thr_bits & 0xF)

    def refresh(self):
        try:
            folder = self.folder_fn()
        except Exception:  # noqa: BLE001
            folder = None
        v = self.monitor.quick(Path(folder) if folder else None)
        self.values = v
        parts = []
        if v.get("cpu_percent") is not None:
            parts.append(f"CPU {_de(v['cpu_percent'])} %")
        if v.get("mem_percent") is not None:
            parts.append(f"RAM {_de(v['mem_percent'])} %")
        if v.get("temp_c") is not None:
            parts.append(f"{_de(v['temp_c'])} °C")
        if v.get("disk_free") is not None:
            parts.append(_("{size} free").format(size=fmt_size(v['disk_free'])))
        self.button.setText("  ·  ".join(parts) or _("System check"))
        warn = self.warning or (v.get("temp_c") or 0) >= 80
        self.button.setIcon(icon("dialog-warning") if warn else icon("utilities-system-monitor", "computer"))
        self.button.setToolTip(self._tooltip())
        if time.time() - self._thr_at > self.THROTTLE_EVERY_S:
            self._thr_at = time.time()
            threading.Thread(target=lambda: self._sig.throttled.emit(*self.monitor.throttled()),
                             daemon=True, name="fm1c-throttled").start()

    def _got_throttled(self, bits, names):
        self.thr_bits, self.thr_names = bits, names or []
        self.button.setToolTip(self._tooltip())
        if self.warning:
            self.button.setIcon(icon("dialog-warning"))

    def _tooltip(self) -> str:
        v = self.values
        rows = []
        if v.get("cpu_percent") is not None:
            rows.append((_("Processor"), f"{_de(v['cpu_percent'])} %"
                         + (_(" at {mhz} MHz").format(mhz=_de(v['freq_mhz'])) if v.get("freq_mhz") else "")))
        if v.get("load"):
            rows.append((_("Load (1/5/15 min)"), " / ".join(_de(x, 2) for x in v["load"])))
        if v.get("mem_percent") is not None:
            rows.append((_("Memory"), _("{percent} % of {total}").format(percent=_de(v['mem_percent']),
                                                                        total=fmt_size(v.get('mem_total')))))
        if v.get("temp_c") is not None:
            rows.append((_("Temperature"), f"{_de(v['temp_c'], 1)} °C"))
        if v.get("disk_free") is not None:
            rows.append((_("FM-1 folder"), _("{free} free of {total}").format(free=fmt_size(v['disk_free']),
                                                                             total=fmt_size(v.get('disk_total')))))
        if v.get("undervoltage"):
            rows.append((_("Power supply"), _("<b>Undervoltage!</b>")))
        if self.thr_bits is not None:
            rows.append((_("Throttling"), ", ".join(self.thr_names) or _("none")))
        body = "".join(f"<tr><td>{a}:&nbsp;</td><td>{b}</td></tr>" for a, b in rows)
        return _("<b>Computer</b><table>{rows}</table><i>Click for the system check</i>").format(rows=body)


class SystemCheckDialog(QDialog):
    """Ampel-Liste mit Erklärungen; läuft im Hintergrund."""
    finished_check = pyqtSignal()

    def __init__(self, ctx=None, parent=None, runner=None):
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner or syscheck.run_all
        self.found: list[syscheck.Finding] = []
        self.meta: dict = {}
        self.setWindowTitle(_("System check"))
        self.resize(780, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("System check"), _("Searches the kernel log and the system for anything that can harm "
                                                   "the FM-1: loose contacts, too little power, USB audio errors, heat.")))
        self.summary = QLabel(_("Checking …"))
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Finding"), _("Level")])
        self.tree.setColumnWidth(0, 600)
        self.tree.setWordWrap(True)
        self.tree.setAlternatingRowColors(False)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.b_again = QPushButton(icon("view-refresh"), _("Check again"))
        self.b_again.clicked.connect(self.run)
        self.b_copy = QPushButton(icon("edit-copy"), _("Copy report"))
        self.b_copy.clicked.connect(self.copy)
        self.b_save = QPushButton(icon("document-save"), _("Save …"))
        self.b_save.clicked.connect(self.save)
        close = QPushButton(_("Close"))
        close.setDefault(True)
        close.clicked.connect(self.accept)
        for b in (self.b_again, self.b_copy, self.b_save):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.run()

    def _folder(self):
        try:
            return self.ctx.settings.root if self.ctx else None
        except Exception:  # noqa: BLE001
            return None

    def _fm1_paths(self) -> set[str]:
        out = set()
        for p in getattr(self.ctx, "ports", []) or []:
            if getattr(p, "usb_path", ""):
                out.add(p.usb_path)
        return out

    def run(self):
        self.b_again.setEnabled(False)
        self.summary.setText(_("Checking kernel log, power supply, USB and audio …"))
        folder, paths = self._folder(), self._fm1_paths()
        run_job(self, lambda w: self.runner(folder, paths), on_done=self.show_result,
                on_fail=lambda m, tb: (self.summary.setText(_("Check failed: {error}").format(error=m)), self.b_again.setEnabled(True)))

    def show_result(self, res):
        self.found, self.meta = res
        self.b_again.setEnabled(True)
        self.tree.clear()
        for f in self.found:
            it = QTreeWidgetItem([f.title, LEVEL_TEXT[f.level]])
            it.setIcon(0, icon(LEVEL_ICON[f.level]))
            it.setData(0, Qt.ItemDataRole.UserRole, f.key)
            fix = _("Solution: {fix}").format(fix=f.fix) if f.fix else ""
            tip = "\n".join(x for x in (f.detail, fix) if x)
            it.setToolTip(0, tip)
            for txt in (f.detail, fix):
                if txt:
                    c = QTreeWidgetItem([txt, ""])
                    c.setFirstColumnSpanned(True)
                    it.addChild(c)
            if f.evidence:
                ev = QTreeWidgetItem([_("Log lines"), ""])
                for e in f.evidence:
                    ev.addChild(QTreeWidgetItem([e, ""]))
                it.addChild(ev)
            self.tree.addTopLevelItem(it)
            if f.level in ("error", "warn", "unknown"):
                it.setExpanded(True)
        n = {lv: sum(1 for f in self.found if f.level == lv) for lv in ("error", "warn")}
        src = {"dmesg": "dmesg", "journalctl": "journalctl -k"}.get(self.meta.get("source"), _("no kernel log"))
        if n["error"]:
            pe = ngettext("{n} problem", "{n} problems", n["error"]).format(n=n["error"])
            wa = ngettext("one warning", "{n} warnings", n["warn"]).format(n=n["warn"])
            head = (_("<b>{problems}</b> and {warnings} found.").format(problems=pe, warnings=wa) if n['warn']
                    else _("<b>{problems}</b> found.").format(problems=pe))
        elif n["warn"]:
            head = _("<b>{warnings}</b>, otherwise everything is fine.").format(
                warnings=ngettext("{n} warning", "{n} warnings", n["warn"]).format(n=n["warn"]))
        else:
            head = _("<b>Everything is fine.</b>")
        self.summary.setText(_("{summary} Source: {source}, checked at {time}.").format(
            summary=head, source=src, time=time.strftime('%H:%M:%S')))
        self.finished_check.emit()

    def report(self) -> str:
        return syscheck.report(self.found, self.meta)

    def copy(self):
        QGuiApplication.clipboard().setText(self.report())

    def save(self, path: str | None = None):
        if not path:
            path, _filter = QFileDialog.getSaveFileName(self, _("Save report"),
                                                  str(Path.home() / f"systemcheck-{time.strftime('%Y-%m-%d_%H-%M')}.txt"),
                                                  "Text (*.txt)")
        if path:
            Path(path).write_text(self.report(), "utf-8")
        return path


__all__ = ["PiStatus", "SystemCheckDialog", "fmt_secs"]
