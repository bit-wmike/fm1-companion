# SPDX-License-Identifier: GPL-3.0-only
"""Firmware: Katalog, Versionen, Installieren, Wechseln, eigene Pakete."""
from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QSizePolicy, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from .. import backup, config, editor_host, firmware
from .common import JobDialog, call_in_gui, confirm, flow_row, heading, icon, init_gui_caller, run_job, show_error
from .responsive import adaptive_splitter


def find_backup_for(ctx, key: str) -> Path | None:
    """jüngstes Backup dieses Geräts, das mit Firmware `key` gemacht wurde."""
    if not ctx.profile:
        return None
    try:
        folder = ctx.device_folder(ctx.settings.backups_dir)
        files = sorted(folder.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    except OSError:
        return None
    best = None
    for f in files:
        try:
            meta = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        if (meta.get("companion") or {}).get("firmware") == key or key in str(meta.get("firmware", "")).lower():
            best = f
            break
    return best


def match_by_sha(sha: str):
    """FirmwareSpec mit dieser Prüfsumme (eingebaut, eigene, Original, Katalog, selbst geprüfte Releases).
    Nie über den Dateinamen – eine umbenannte fremde Datei bleibt „unbekannt“."""
    sha = (sha or "").lower()
    for sp in firmware.catalog() + firmware.STOCK:
        if sp.sha256 and sp.sha256.lower() == sha:
            return sp
    try:
        from .. import catalog as cat
        fn = getattr(cat, "spec_by_sha256", None)
        if fn:
            sp = fn(sha)
            if sp is not None:
                return sp
    except Exception:  # noqa: BLE001
        pass
    return firmware.spec_by_release_sha(sha)


def file_matches(spec, info) -> bool:
    """ist diese Datei nachweislich (Prüfsumme) die Firmware `spec`?"""
    if spec.sha256 and info.sha256 == spec.sha256.lower():
        return True
    if info.official and info.official == spec.product:
        return True
    m = match_by_sha(info.sha256)
    return m is not None and m.key == spec.key


def device_count(ctx) -> int:
    """wie viele FM-1 (auch im Update-Modus) gerade angeschlossen sind – ein Gerät je USB-Anschluss"""
    seen = set()
    for p in ctx.ports:
        seen.add(p.usb_path or p.key)
    return len(seen) + len(getattr(ctx, "uboot", []) or [])


def target_pin(ctx):
    """das aktive Gerät über USB-Anschluss (sysfs) oder eindeutige Seriennummer festhalten – None, wenn das nicht
    geht (dann darf bei mehreren FM-1 nicht installiert werden)"""
    from ..devices import usable_serial
    from ..flash.engine import Pin
    p = ctx.port
    if p is None or getattr(ctx, "is_virtual", False):      # Studio-Zwilling: nie ein Flash-Ziel
        return None
    if p.usb_path:
        return Pin(usb_path=p.usb_path)
    serial = usable_serial(p, ctx.ports or [p])
    return Pin(serial=serial) if serial else None


def device_state(ctx) -> "planner.DeviceState":
    from ..flash import journal, planner
    return planner.DeviceState(identity=getattr(ctx, "identity", "") or (ctx.profile.identity if ctx.profile else ""),
                               felucca=bool(ctx.connected), firmware_key=ctx.firmware_key,
                               version=ctx.info.version if ctx.info else (ctx.profile.version_string if ctx.profile
                                                                          else ""),
                               port=ctx.port.in_name if ctx.port else "", connected=ctx.port is not None,
                               ports=device_count(ctx), busy=journal.Journal.busy(),
                               pinned=target_pin(ctx) is not None)


from ..flash.planner import V16_WARNING as V16_WARNING_HTML  # noqa: E402

RISK_COLOR = {"keins": "#27ae60", "niedrig": "#27ae60", "mittel": "#f67400", "hoch": "#da4453"}


class WrapCheck(QWidget):
    """Kontrollkästchen mit umbrechendem Text (QCheckBox selbst bricht nicht um und schneidet ab)"""
    toggled = pyqtSignal(bool)

    def __init__(self, text: str, bold: bool = False, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.box = QCheckBox()
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.label.setMinimumWidth(1)              # darf schmaler werden als der Text – dann bricht er um
        # oben neben dem Kästchen, auch wenn das Layout mehr Höhe gibt (sonst rutscht der Text eine Zeile tiefer)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        if bold:
            f = self.label.font()
            f.setBold(True)
            self.label.setFont(f)
        self.label.mousePressEvent = lambda e: self.box.isEnabled() and self.box.toggle()
        h.addWidget(self.box, 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(self.label, 1)
        self.box.toggled.connect(self.toggled.emit)

    def isChecked(self) -> bool:
        return self.box.isChecked()

    def setChecked(self, on: bool):
        self.box.setChecked(on)

    def text(self) -> str:
        return self.label.text()

    def setText(self, t: str):
        self.label.setText(t)

    def setToolTip(self, t: str):
        super().setToolTip(t)
        self.box.setToolTip(t)
        self.label.setToolTip(t)

    def setEnabled(self, on: bool):
        super().setEnabled(on)
        self.box.setEnabled(on)


class PlanDialog(QDialog):
    """Vorschau des Installationsplans: Schritte, Warnungen, was verloren geht, Bestätigungen, Probelauf."""

    def __init__(self, parent, plan_fn, target_label: str, backup_default: bool, via_default: bool | None):
        super().__init__(parent)
        from ..flash import planner
        self.planner = planner
        self.plan_fn = plan_fn
        self.setWindowTitle("Installationsplan")
        self.setMinimumSize(820, 680)
        self.resize(880, 740)
        lay = QVBoxLayout(self)
        head = QLabel(f"<h3>{target_label}</h3>")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.view = QTextBrowser()
        lay.addWidget(self.view, 1)
        self.lost_label = QLabel()                 # „Was danach nicht mehr auf dem Gerät ist“ – immer sichtbar
        self.lost_label.setWordWrap(True)
        self.lost_label.setTextFormat(Qt.TextFormat.RichText)
        self.lost_label.setObjectName("PlanLost")
        self.lost_label.setStyleSheet("QLabel#PlanLost { border: 1px solid #f67400; border-radius: 6px; padding: 6px; }")
        self.lost_label.hide()
        lay.addWidget(self.lost_label)
        self.chk_v15 = WrapCheck("Zwischenschritt: Original-V15 aufspielen")
        self.chk_v15.setToolTip("Über die unveränderte Original-Firmware V15 startet die neue Firmware von einem "
                                "sauberen, bewährten Stand. Der Zwischenschritt ist selbst eine Installation: Strom und "
                                "Kabel müssen dann zweimal durchhalten statt einmal.")
        self.v15_note = QLabel()
        self.v15_note.setWordWrap(True)
        lay.addWidget(self.chk_v15)
        lay.addWidget(self.v15_note)
        self.chk_backup = WrapCheck("Vorher ein Backup machen (empfohlen)")
        self.chk_backup.setChecked(backup_default)
        lay.addWidget(self.chk_backup)
        self.backup_note = QLabel()
        self.backup_note.setWordWrap(True)
        lay.addWidget(self.backup_note)
        self.confirm_box = QVBoxLayout()
        lay.addLayout(self.confirm_box)
        self.chk_dry = WrapCheck("Nur Probelauf: alles prüfen, nichts schreiben")
        lay.addWidget(self.chk_dry)
        self.bb = QDialogButtonBox()
        self.ok_btn = self.bb.addButton("Installieren", QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok_btn.setProperty("variant", "primary")
        self.bb.addButton("Abbrechen", QDialogButtonBox.ButtonRole.RejectRole)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        self.ok_hint = QLabel("Bitte oben bestätigen")
        self.ok_hint.setProperty("role", "muted")
        brow = QHBoxLayout()
        brow.addWidget(self.ok_hint)
        brow.addStretch(1)
        brow.addWidget(self.bb)
        lay.addLayout(brow)
        self.confirm_checks: dict[str, WrapCheck] = {}
        self._via = via_default
        self.plan = None
        self.refresh()
        self.chk_v15.toggled.connect(self._v15_toggled)
        self.chk_backup.toggled.connect(lambda _: self.refresh())
        self.chk_dry.toggled.connect(lambda on: (self.ok_btn.setText("Probelauf starten" if on else "Installieren"),
                                                 self._update_ok()))

    def _v15_toggled(self, on):
        self._via = on
        self.refresh()

    def _call_plan(self):
        try:
            return self.plan_fn(self._via, self.chk_backup.isChecked())
        except TypeError:                          # ältere Aufrufer: plan_fn(via)
            return self.plan_fn(self._via)

    def refresh(self):
        p = self.plan = self._call_plan()
        opt = p.v15_option
        self.chk_v15.blockSignals(True)
        self.chk_v15.box.blockSignals(True)
        self.chk_v15.setVisible(opt != "none")
        self.chk_v15.setChecked(p.via_stock)
        self.chk_v15.setEnabled(opt != "locked")
        self.chk_v15.box.blockSignals(False)
        self.chk_v15.blockSignals(False)
        self.v15_note.setVisible(opt != "none")
        self.v15_note.setText(p.v15_reason or {
            "locked": "Pflicht: Diese Firmware wird laut Katalog nur von der Original-V15 aus installiert.",
            "default_on": "Firmware-Wechsel (oder laufende Firmware unbekannt): Der Zwischenschritt ist "
                          "voreingestellt, um Flüchtigkeitsfehler beim schnellen Hin- und Herwechseln zu vermeiden. "
                          "Er ist aber selbst ein Schreibvorgang – zwei Installationen statt einer, also zweimal ein "
                          "Zeitraum, in dem Akku und Kabel halten müssen. Nötig ist er nur bei V16 und bei Firmwares, "
                          "die laut Katalog nur von V15 aus gehen (dann ist er fest). Nur abwählen, wenn du sicher bist.",
            "default_off": "Update derselben Firmware: normalerweise nicht nötig."}.get(opt, ""))
        can_backup = p.state.felucca and not p.resume
        self.chk_backup.setVisible(can_backup)
        self.chk_backup.setText("Vorher ein Backup machen (Pflicht beim Wechsel mit V16)"
                                if self.planner.layout_change(p.state, p.target) else
                                "Vorher ein Backup machen (empfohlen)")
        self.backup_note.setVisible(not p.resume)
        self.backup_note.setText(
            ("" if self.chk_backup.isChecked() else
             "Ohne Backup: Das musst du unten ausdrücklich bestätigen.") if can_backup else
            "Backup: Von der laufenden Firmware kann die App kein Backup machen (kein Editor-Protokoll). "
            "Wichtige Projekte vorher mit dem Werkzeug der Firmware sichern.")
        rows = "".join(
            f"<tr><td><b>{n}.</b></td><td><b>{st.title}</b><br><small>{st.text}</small></td>"
            f"<td style='color:{RISK_COLOR.get(st.risk, '#888')}'>{self.planner.RISK_TEXT.get(st.risk, st.risk)}"
            f"{' · schreibt' if st.writes else ''}</td></tr>" for n, st in enumerate(p.steps, 1))
        html = f"<table cellspacing=6>{rows}</table>"
        if p.blockers:
            html += "<h4 style='color:#da4453'>Nicht möglich</h4><ul>" + "".join(f"<li>{b}</li>" for b in p.blockers) + "</ul>"
        if p.warnings:
            html += "<h4>Hinweise</h4><ul>" + "".join(f"<li>{w}</li>" for w in p.warnings) + "</ul>"
        html += "<p><small>Während des Schreibens das USB-Kabel nicht abziehen und das FM-1 nicht ausschalten.</small></p>"
        self.view.setHtml(html)
        self.lost_label.setVisible(bool(p.lost))
        if p.lost:
            self.lost_label.setText("<b>Was danach nicht mehr auf dem Gerät ist:</b> " + " · ".join(p.lost))
        for c in list(self.confirm_checks):
            if c not in p.confirmations:
                cb = self.confirm_checks.pop(c)
                self.confirm_box.removeWidget(cb)
                cb.deleteLater()
        for c in p.confirmations:
            if c not in self.confirm_checks:
                cb = WrapCheck(self.planner.CONFIRM_TEXT.get(c, c), bold=True)
                cb.toggled.connect(lambda _: self._update_ok())
                self.confirm_box.addWidget(cb)
                self.confirm_checks[c] = cb
        self._update_ok()

    def confirmed(self) -> list[str]:
        return [c for c, cb in self.confirm_checks.items() if cb.isChecked()]

    def missing(self) -> list[str]:
        return [c for c in self.plan.missing(self.confirmed()) if not (c == "no_backup" and self.chk_dry.isChecked())]

    def _update_ok(self):
        ok = self.plan.ok and not self.missing()
        self.ok_btn.setEnabled(ok)
        self.ok_hint.setVisible(not ok)
        self.ok_hint.setText("Nicht möglich – siehe oben" if not self.plan.ok else "Bitte oben bestätigen")


class InstallDialog(JobDialog):
    """Fortschritt mit Protokoll; Abbrechen nur, solange noch nichts gesendet wurde. Während das FM-1 im Update-Modus
    schreibt: roter Hinweis „Jetzt nicht abstecken“, Abbrechen gesperrt."""

    CANCEL_TIP = ("Bricht vor dem nächsten sicheren Punkt ab. Während geschrieben wird, läuft die Installation zu Ende – "
                  "ein Abbruch dort würde das FM-1 im Update-Modus lassen.")

    def __init__(self, parent, title, fn, cancel_event, **kw):
        self.cancel_event = cancel_event
        self.writing = False
        self.danger = QLabel("<b>Jetzt nicht abstecken und nicht ausschalten.</b> Das FM-1 schreibt gerade seine "
                             "Firmware. Das dauert ein paar Minuten – danach startet es von selbst neu.")
        self.danger.setObjectName("InstallDanger")
        self.danger.setWordWrap(True)
        self.danger.setStyleSheet("QLabel#InstallDanger { color: #ffffff; background: #da4453; border-radius: 6px; "
                                  "padding: 8px; }")
        self.danger.hide()
        super().__init__(parent, title, fn, **kw)
        self.layout().insertWidget(1, self.danger)
        self.btn_cancel = self.buttons.addButton("Abbrechen (nur vor dem Schreiben)",
                                                 QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_cancel.setToolTip(self.CANCEL_TIP)
        self.btn_cancel.clicked.connect(self._cancel)

    def add(self, line: str):
        super().add(line)
        from ..flash import engine as fe
        if line.startswith(fe.LOG_WRITING):
            self._set_writing(True)
        elif line.startswith(fe.LOG_REBOOT):
            self._set_writing(False)

    def _set_writing(self, on: bool):
        self.writing = on
        self.danger.setVisible(on)
        if hasattr(self, "btn_cancel"):
            self.btn_cancel.setEnabled(not on and not self.cancel_event.is_set())
            self.btn_cancel.setToolTip("Während das FM-1 schreibt, lässt sich nicht sicher abbrechen – bitte warten."
                                       if on else self.CANCEL_TIP)

    def _cancel(self):
        if self.writing:
            return
        self.cancel_event.set()
        self.add("Abbruch angefordert – greift am nächsten sicheren Punkt.")
        self.btn_cancel.setEnabled(False)

    def _done(self, res):
        self.writing = False
        self.danger.hide()
        self.btn_cancel.setEnabled(False)
        super()._done(res)

    def _fail(self, *a):
        self.writing = False
        self.danger.hide()
        self.btn_cancel.setEnabled(False)
        super()._fail(*a)
        if self.cancel_event.is_set() and str(a[0] if a else "").startswith("Abgebrochen"):
            self._state(False, "Abgebrochen", self.result_label.text())   # gewollt – kein „Fehlgeschlagen“


def _install(*a, **kw):
    """firmware.install mit den Zusatzangaben, die die aktuelle Fassung kennt (Tests ersetzen sie teils)"""
    import inspect
    fn = firmware.install
    try:
        params = inspect.signature(fn).parameters
        if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
            kw = {k: v for k, v in kw.items() if k in params}
    except (TypeError, ValueError):
        pass
    return fn(*a, **kw)


def german_errors(work):
    """Fehler im Hintergrund-Job als deutscher Satz; die technischen Details landen im Protokoll"""
    def run(w, *a, **k):
        try:
            return work(w, *a, **k)
        except Exception as e:  # noqa: BLE001
            if type(e) in (RuntimeError, PackageRefused):      # schon ein fertiger deutscher Satz
                raise
            w.log.emit("Technische Details: " + firmware.explain(e))
            raise RuntimeError(firmware.friendly_error(e)) from e
    return run


class PackageRefused(RuntimeError):
    pass


def validate_package(path, spec, known: bool, label: str, expect_sha: str = "", official: str = "", log=None):
    """ein Paket vollständig prüfen, BEVOR irgendetwas geschrieben wird: Kennung, Prüfsumme, Modell, Unversehrtheit,
    Loader-Familie und -Regel. -> PackageInfo; PackageRefused mit deutschem Grund."""
    from ..flash import package as fpk, planner
    try:
        pi = fpk.analyze(path, spec.loader_family)
    except fpk.PackageError as e:
        raise PackageRefused(f"{label}: {e}")
    t = planner.Target.from_spec(spec)
    t.known = known
    probs = planner.check_package(t, pi)
    if pi.loader_rule is None and not any("Loader" in x for x in probs):
        probs.append("Die Loader-Familie des Pakets ist unbekannt – es wird an keinen Update-Loader geschickt.")
    if official and pi.official != official:
        probs.append(f"Das ist nicht die unveränderte Original-Firmware ({official}).")
    if expect_sha and pi.sha256 != expect_sha.lower():
        probs.append("Die Datei hat sich seit dem Plan verändert (andere Prüfsumme).")
    if log:
        log(f"{label}: {pi.product}, SHA-256 {pi.sha256[:16]}…, Loader-Familie {pi.family}"
            + (f", BESCHÄDIGT ({pi.damage})" if pi.damage else "") + (" – in Ordnung" if not probs else ""))
    if probs:
        raise PackageRefused(f"{label} wird nicht installiert: " + " ".join(probs))
    return pi


def install_flow(parent, ctx, spec: firmware.FirmwareSpec, rel: firmware.Release | None = None,
                 local_pkg: Path | None = None, tag: str = "", dry_run: bool | None = None) -> bool:
    """kompletter Ablauf: Plan (Vorschau, Bestätigungen) → ALLE Pakete laden und prüfen → Backup → (V15) →
    Installieren → Ressourcen → Identität → Profil/Verlauf aktualisieren. Geschrieben wird erst, wenn jedes
    Paket des Plans geprüft ist; das Zielgerät wird über seinen USB-Anschluss festgehalten."""
    import threading
    from ..flash import package as fpk, planner, prefs
    s = ctx.settings
    if getattr(ctx, "is_virtual", False):
        show_error(parent, "Kein echtes FM-1", "Der Studio-Zwilling ist kein echtes FM-1 – Firmware wird nur auf ein angeschlossenes Gerät installiert. Bitte oben ein echtes FM-1 wählen.")
        return False
    if not ctx.port:
        show_error(parent, "Kein Gerät", "Bitte das FM-1 per USB anschließen.")
        return False
    from .risk_dialog import ensure_risk_ack
    if not ensure_risk_ack(parent):              # einmaliger Risiko-Hinweis vor der ersten Installation
        return False
    target = rel.tag if rel else (tag or "eigene Datei")
    info = None
    if local_pkg is not None:
        try:
            info = fpk.analyze(local_pkg, spec.loader_family)
        except fpk.PackageError as e:
            show_error(parent, "Kein FM-1-Paket", str(e))
            return False
    state = device_state(ctx)
    pin = target_pin(ctx)
    planned_port = ctx.port                      # das Gerät, für das der Plan gilt
    tgt = planner.Target.from_spec(spec, tag=rel.tag if rel else tag, file=local_pkg, package=info,
                                   prerelease=bool(rel and rel.prerelease))
    if local_pkg is not None:
        # eigene Datei: bekannt nur, wenn die Prüfsumme passt (Katalog, Release-SHA256SUMS, Original) – nie per Name
        tgt.known = file_matches(spec, info)
    else:
        tgt.checksum = bool(spec.sha256) or bool(rel and spec.sums and spec.sums in rel.assets)
    key = planner.switch_key(state, tgt)
    remembered = prefs.choice_for(key)

    def make(via, backup=True):
        return planner.plan_install(state, tgt, via_v15=via, backup=backup)
    dlg = PlanDialog(parent, make, f"{firmware.display_label(spec, target if rel or tag else '')} auf "
                                   f"{ctx.profile.name if ctx.profile else 'dieses FM-1'}",
                     bool(s.auto_backup_before_update) and ctx.connected, remembered)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    plan = dlg.plan
    if not plan.ok:
        show_error(parent, "Installation nicht möglich", "\n".join(re.sub("<[^>]+>", "", b) for b in plan.blockers))
        return False
    if dlg.missing():
        show_error(parent, "Bestätigung fehlt", "Bitte im Plan alle fett gedruckten Punkte ausdrücklich bestätigen:\n\n"
                   + "\n".join("• " + planner.CONFIRM_TEXT.get(c, c) for c in dlg.missing()))
        return False
    if plan.v15_option in ("default_on", "default_off"):
        prefs.remember(key, plan.via_stock)
    do_dry = dlg.chk_dry.isChecked() if dry_run is None else dry_run
    # Während der Plan offen war, lief die Geräteerkennung weiter: Ist es noch dasselbe FM-1 im selben Zustand?
    if ctx.port is None or planned_port is None or ctx.port.key != planned_port.key:
        show_error(parent, "Gerät hat sich geändert",
                   "Während der Plan offen war, wurde das FM-1 getrennt oder ein anderes Gerät aktiv. Es wurde nichts "
                   "geschrieben. Bitte das gewünschte FM-1 anschließen bzw. oben links auswählen und den Plan neu öffnen.")
        return False
    if "backup" in plan.kinds() and not do_dry and not ctx.connected:
        show_error(parent, "Backup nicht möglich",
                   "Der Plan sieht ein Backup vor, aber die Verbindung zum FM-1 ist inzwischen weg. Es wurde nichts "
                   "geschrieben. Bitte neu verbinden und den Plan erneut öffnen.")
        return False
    do_backup = "backup" in plan.kinds() and ctx.connected and not do_dry
    link = ctx.link
    port_name = ctx.port.in_name
    profile = ctx.profile
    cur_key = ctx.firmware_key
    switching = cur_key != spec.key
    old_fw = cur_key
    info_version = ctx.info.version if ctx.info else ""
    try:
        bk_folder = ctx.device_folder(s.backups_dir) if profile else s.backups_dir
    except OSError as e:
        if do_backup:
            show_error(parent, "Backup-Ordner nicht beschreibbar", firmware.friendly_error(e) if not str(e).startswith(
                "Der Ordner") else str(e))
            return False
        bk_folder = None
    via_stock = plan.via_stock
    known = tgt.known
    local_sha = info.sha256 if info is not None else ""
    label = firmware.display_label(spec, tag if not rel else rel.tag)
    cancel = threading.Event()
    written: list[str] = []
    ctx.hold = True

    def cancel_text() -> str:
        if not written:
            return "Abgebrochen – am FM-1 wurde nichts geändert."
        return (f"Abgebrochen nach dem Zwischenschritt: Auf das FM-1 wurde {', '.join(written)} geschrieben – es "
                f"läuft jetzt die Original-Firmware V15. {spec.name} wurde NICHT geschrieben. Zum Fortsetzen "
                f"{spec.name} erneut installieren (der Zwischenschritt ist dann nicht mehr nötig).")

    def check_cancel():
        if cancel.is_set():
            raise RuntimeError(cancel_text())

    def work(w):
        # ---- 1. jedes Paket des Plans laden und vollständig prüfen – noch nichts am Gerät
        w.progress.emit(0, 0)
        w.log.emit("Schritt: alle Pakete laden & prüfen (vor jedem Schreiben) …")
        stock_spec = firmware.spec("stock-v15")
        stock_info = None
        try:
            if via_stock:
                st_pkg = firmware.download(stock_spec, firmware.fetch_releases(stock_spec)[0], log=w.log.emit)
                stock_info = validate_package(st_pkg.fwsc, stock_spec, True, "Original-V15", official="FM-1_015",
                                              log=w.log.emit)
            if rel:
                pkg = firmware.download(spec, rel, log=w.log.emit, progress=lambda a, b: w.progress.emit(a, b))
                path, res, t = pkg.fwsc, pkg.res, rel.tag
                if not pkg.verified and not spec.sha256:
                    w.log.emit("Prüfsumme nicht verfügbar: dieses Release hat keine Prüfsummen-Datei (im Plan bestätigt).")
            else:
                path, res, t = local_pkg, None, tag
                if spec.resources:
                    cand = list(path.parent.glob("*-res.bin"))
                    res = cand[0] if cand else None
            tgt_info = validate_package(path, spec, known, firmware.display_label(spec, t), expect_sha=local_sha,
                                        log=w.log.emit)
        except PackageRefused:
            raise
        except Exception as e:  # noqa: BLE001 – Netz/Datei: vor jedem Schreiben, also sagen wir das auch
            w.log.emit("Technische Details: " + firmware.explain(e))
            raise RuntimeError(f"{firmware.friendly_error(e)} Am FM-1 wurde nichts geändert.") from e
        w.log.emit("Alle Pakete geprüft.")
        check_cancel()
        # ---- 2. Backup (nur lesen). Wenn es gewünscht war und scheitert: immer abbrechen.
        if do_backup:
            w.log.emit("Schritt: Backup des Geräts …")
            if link is None or link.closed:
                raise RuntimeError("Backup nicht möglich: Die Verbindung zum FM-1 ist weg. Installation abgebrochen – "
                                   "am FM-1 wurde nichts geändert.")
            try:
                from .. import autobackup
                with link.exclusive():
                    bpath = autobackup.capture_to_file(
                        link, bk_folder, profile, old_fw, info_version,
                        note=f"automatisch vor Installation von {label}", kind="vor-Update",
                        progress=lambda a, b: w.progress.emit(a, b))
                w.log.emit(f"Backup gespeichert: {bpath.name}")
            except Exception as e:  # noqa: BLE001
                w.log.emit("Technische Details: " + firmware.explain(e))
                raise RuntimeError("Backup fehlgeschlagen – Installation abgebrochen, am FM-1 wurde nichts geändert. "
                                   f"Grund: {firmware.friendly_error(e)} Wer ohne Backup installieren will, wählt im "
                                   "Plan „Vorher ein Backup machen“ ab und bestätigt das.")
        check_cancel()
        if link and not do_dry:
            link.close()
        # ---- 3. Probelauf
        if do_dry:
            from ..flash import engine as fe
            e = fe.Engine(log=w.log.emit)
            e.write(stock_info or tgt_info, port=port_name, dry_run=True, pin=pin)
            w.log.emit("Probelauf beendet – es wurde nichts geschrieben.")
            return {"dry": True}
        check_cancel()
        # ---- 4. Schreiben
        if stock_info is not None:
            w.log.emit("Schritt: Zwischenschritt Original-V15 …")
            w.progress.emit(0, 100)
            _install(stock_spec, stock_info.path, "V15", port_name=port_name, log=w.log.emit,
                     progress=lambda pct: w.progress.emit(pct, 100),
                     device_id=profile.id if profile else "", cancel=cancel, pin=pin, expect_sha=stock_info.sha256)
            written.append("die Original-Firmware V15")
            w.log.emit("Original-V15 läuft. Weiter mit der Zielfirmware …")
            check_cancel()
        w.log.emit(f"Schritt: {spec.name} aufspielen …")
        w.progress.emit(0, 100)
        report: dict = {}
        try:
            ident = _install(spec, tgt_info.path, t, port_name=port_name, res=res, log=w.log.emit,
                             progress=lambda pct: w.progress.emit(pct, 100),
                             device_id=profile.id if profile else "", confirmed_unknown=True,
                             cancel=cancel, pin=pin, expect_sha=tgt_info.sha256, report=report)
        except Exception as e:  # noqa: BLE001
            if written:
                w.log.emit("Technische Details: " + firmware.explain(e))
                if getattr(e, "code", "") == "cancelled" or cancel.is_set():
                    raise RuntimeError(cancel_text()) from e
                st = getattr(e, "state", "safe")
                if st == "paused":
                    now = (f"Das FM-1 wartet jetzt im Update-Modus – {spec.name} ist nicht vollständig geschrieben. "
                           "Nichts abziehen: auf der Seite „Rettung“ mit „Installation abschließen“ weitermachen.")
                elif st == "restart":
                    now = (f"{spec.name} wurde geschrieben, aber das FM-1 hat sich danach nicht wie erwartet gemeldet. "
                           "Aus- und wieder einschalten und prüfen, welche Firmware es zeigt.")
                else:
                    now = f"Auf dem FM-1 läuft jetzt die Original-Firmware V15; {spec.name} wurde nicht geschrieben."
                raise RuntimeError(f"{firmware.friendly_error(e)} Vorher geschrieben: {', '.join(written)}. {now}") from e
            raise
        w.log.emit(f"Gerät meldet jetzt: {ident or 'unbekannt'}")
        if spec.protocol == "felucca" and spec.repo and spec.editor:
            try:
                editor_host.prepare(spec, t, log=w.log.emit)
            except Exception as e:  # noqa: BLE001
                w.log.emit(f"Editor wird später bereitgestellt ({firmware.friendly_error(e)})")
        return {"ident": ident, "tag": t, "resources": report.get("resources", ""), "backup": bool(do_backup),
                "via": bool(stock_info is not None)}

    def summary(res) -> str:
        if res.get("dry"):
            return ("Probelauf erfolgreich – alle Pakete geprüft, das FM-1 hat geantwortet. Am FM-1 wurde nichts "
                    "geändert.")
        parts = [f"Installation abgeschlossen – das FM-1 meldet jetzt {res.get('ident') or 'eine unbekannte Kennung'} "
                 f"({label})"]
        if res.get("via"):
            parts.append("über die Original-V15")
        if res.get("resources"):
            parts.append(res["resources"])
        parts.append("Backup vorher gespeichert" if res.get("backup") else "ohne Backup")
        text = ", ".join(parts) + "."
        if spec.key.startswith("stock") or via_stock:
            text += (f" Geprüft hat die App die Prüfsumme des Pakets und die Kennung nach dem Neustart "
                     f"({res.get('ident') or '?'}). Ob jedes Byte im Flash stimmt, lässt sich nur mit einem Flash-Dump "
                     "prüfen (Rettung › „Ist mein FM-1 sauber?“).")
        return text

    def done(res):
        if res.get("dry"):
            return
        if profile:
            profile.firmware = spec.key
            profile.firmware_version = res["tag"] if rel or tag else ""
            profile.identity = res["ident"] or profile.identity
            if res["ident"]:
                profile.firmware_choice[res["ident"]] = spec.key
            hist = getattr(profile, "firmware_history", None)
            if isinstance(hist, list):
                hist.append({"time": time.time(), "firmware": spec.key, "name": spec.name, "version": res["tag"],
                             "identity": res["ident"], "via_v15": via_stock})
            ctx.store.save()
        ctx.pending_restore_check = spec.key if switching else ""

    from . import feedback
    try:
        d = feedback.run(parent, f"{label} {'prüfen (Probelauf)' if do_dry else 'installieren'}", german_errors(work),
                         cancel, dialog_cls=InstallDialog, on_done=done, done=summary)
    finally:
        ctx.disconnect(None)
        ctx.reconnect_soon(2500)      # hebt auch ctx.hold wieder auf
    return d.ok


class AddFirmwareDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Firmware hinzufügen")
        f = QFormLayout(self)
        self.name = QLineEdit()
        self.repo = QLineEdit()
        self.repo.setPlaceholderText("besitzer/repo auf GitHub, z. B. charlesvestal/fm1-x0x")
        self.asset = QLineEdit()
        self.asset.setPlaceholderText("Paketname im Release, {ver} = Version, z. B. x0x-{ver}.fwsc")
        self.res = QLineEdit()
        self.res.setPlaceholderText("optional: Ressourcen-Datei, z. B. name-{ver}-res.bin")
        self.desc = QLineEdit()
        f.addRow("Name:", self.name)
        f.addRow("GitHub-Repo:", self.repo)
        f.addRow("Paket:", self.asset)
        f.addRow("Ressourcen:", self.res)
        f.addRow("Beschreibung:", self.desc)
        hint = QLabel("Jede FM-1-Firmware lässt sich installieren: Die App benutzt den eingebauten Installer und prüft "
                      "Paket-Kennung und Update-Loader. Backups, Projekte und Editor gibt es nur bei Firmwares der "
                      "Felucca-Familie mit Editor-Protokoll.")
        hint.setWordWrap(True)
        f.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def entry(self) -> dict | None:
        name, repo, asset = self.name.text().strip(), self.repo.text().strip().strip("/"), self.asset.text().strip()
        if not (name and re.fullmatch(r"[\w.-]+/[\w.-]+", repo) and "{ver}" in asset):
            return None
        key = re.sub(r"\W+", "", name.lower()) or "custom"
        return {"key": key, "name": name, "repo": repo, "asset": asset, "resources": self.res.text().strip(),
                "resource_tool": "tools/fm1_resources.py" if self.res.text().strip() else "",
                "description": self.desc.text().strip()}


def _date_text(iso: str) -> str:
    """Veröffentlichungsdatum fürs Auge (TT.MM.JJJJ), "–" wenn unbekannt."""
    if not iso:
        return "–"
    try:
        import datetime as _dt
        return _dt.date.fromisoformat(iso[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return iso


class FirmwarePage(QWidget):
    """Installieren & Versionen – Reiter der Seite „Firmware+“ (früher eigene Seite „Firmware“; der Seiten-Schlüssel
    "firmware" leitet weiter auf diesen Reiter)."""
    updates_checked = pyqtSignal(dict)
    list_changed = pyqtSignal()                # Firmware hinzugefügt/entfernt – der Katalog-Reiter zieht nach

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.releases: dict[str, list[firmware.Release]] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Installieren & Versionen", "Versionen prüfen, aktualisieren oder zu einer anderen "
                                          "Firmware wechseln. "
                                          "Jede FM-1-Firmware: mit Installationsplan, Prüfsumme, Probelauf und dem "
                                          "passenden Update-Loader."))
        split = QSplitter()
        lay.addWidget(split, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.cat = QListWidget()
        self.cat.currentRowChanged.connect(self._select)
        ll.addWidget(self.cat, 1)
        b_add = QPushButton(icon("list-add"), "Firmware hinzufügen …")
        b_add.clicked.connect(self._add)
        b_del = QPushButton(icon("list-remove"), "Entfernen")
        b_del.clicked.connect(self._remove)
        ll.addLayout(_row(b_add, b_del))
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setOpenExternalLinks(True)
        rl.addWidget(self.desc)
        self.v16_warn = QLabel("<b>Achtung V16:</b> " + V16_WARNING_HTML)
        self.v16_warn.setWordWrap(True)
        self.v16_warn.setStyleSheet("QLabel { color: #da4453; border: 1px solid #da4453; border-radius: 4px; "
                                    "padding: 6px; }")
        self.v16_warn.setVisible(False)
        rl.addWidget(self.v16_warn)
        # Gerät läuft V16: geführter Rückweg (über V15, danach Zielfirmware und bei Melodee die Ressourcen)
        self.v16_back = QWidget()
        self.v16_back.setObjectName("V16Back")
        vb = QVBoxLayout(self.v16_back)
        vb.setContentsMargins(0, 0, 0, 0)
        lab = QLabel("<b>Auf diesem FM-1 läuft V16 (experimentell).</b> Der Rückweg geht über die Original-V15: Die App "
                     "spielt erst V15 auf, dann deine Zielfirmware und bei Melodee danach Schrift und Werkssounds.")
        lab.setWordWrap(True)
        vb.addWidget(lab)
        self.b_v16_back = QPushButton(icon("edit-undo", "go-previous"), "Rückweg von V16 …")
        self.b_v16_back.setProperty("variant", "primary")
        self.b_v16_back.setToolTip("Zielfirmware und Version wählen – danach zeigt der Installationsplan jeden Schritt")
        self.b_v16_back.clicked.connect(self.return_from_v16)
        vb.addWidget(flow_row(self.b_v16_back))
        self.v16_back.setVisible(False)
        rl.addWidget(self.v16_back)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Version", "Datum", "Art", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setWordWrap(True)
        self.loading: set[str] = set()
        self.tree.currentItemChanged.connect(self._release_selected)
        self.tree.itemDoubleClicked.connect(lambda *_: self._install())
        rl.addWidget(self.tree, 2)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        rl.addWidget(self.notes, 3)
        self.b_install = QPushButton(icon("system-software-update", "go-down"), "Auf aktives FM-1 installieren")
        self.b_install.setProperty("variant", "primary")
        self.b_install.clicked.connect(self._install)
        self.b_dl = QPushButton(icon("download", "go-down"), "Nur herunterladen")
        self.b_dl.clicked.connect(self._download)
        self.b_refresh = QPushButton(icon("view-refresh"), "Neu laden")
        self.b_refresh.clicked.connect(lambda: self.load_releases(force=True))
        self.chk_rc = QCheckBox("Release Candidates zeigen")
        self.chk_rc.setChecked(ctx.settings.channel == "rc")
        self.chk_rc.toggled.connect(self._fill)
        rl.addWidget(flow_row(self.b_install, self.b_dl, self.b_refresh, self.chk_rc))     # bricht bei Schmalheit um
        self.b_local = QPushButton(icon("document-open"), "Eigene .fwsc-Datei installieren …")
        self.b_local.clicked.connect(self._install_local)
        self.b_res = QPushButton(icon("run-build", "system-run"), "Schrift && Werkssounds nachschreiben")
        self.b_res.setToolTip("Melodee ab 1.6: die Ressourcen-Datei (Schrift, Werkssounds, Bluetooth-Programm) "
                              "nachträglich aufs FM-1 schreiben – z. B. wenn das Display nach dem Update leer bleibt")
        self.b_res.clicked.connect(self._resources)
        self.b_stock = QPushButton(icon("edit-undo", "go-previous"), "Zurück zum Original …")
        self.b_stock.setToolTip("M-VAVEs Original-Firmware V15 oder V16 aufspielen (Prüfsumme wird immer kontrolliert)")
        self.b_stock.clicked.connect(self.return_to_original)
        rl.addWidget(flow_row(self.b_local, self.b_stock, self.b_res))
        self.history = QLabel()
        self.history.setWordWrap(True)
        self.history.setTextFormat(Qt.TextFormat.RichText)
        rl.addWidget(self.history)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        adaptive_splitter(split)                 # schmal: Firmware-Liste über den Details

        self._fill_catalog()
        self._last_fw_key = self.ctx.firmware_key
        ctx.changed.connect(self._follow_device)
        ctx.changed.connect(lambda: self.v16_back.setVisible(getattr(self.ctx, "identity", "") == "FM-1_016"))
        ctx.changed.connect(self._mark)
        ctx.changed.connect(self._show_history)
        ctx.changed.connect(self._release_selected)
        self._show_history()
        self._release_selected()

    def _follow_device(self):
        """anderes Gerät mit anderer Firmware aktiv: dessen Katalogeintrag wählen (Versionen, Status)."""
        key = self.ctx.firmware_key
        if key == self._last_fw_key:
            return
        self._last_fw_key = key
        if not key:
            return
        for i in range(self.cat.count()):
            if self.cat.item(i).data(Qt.ItemDataRole.UserRole) == key:
                if self.cat.currentRow() != i:
                    self.cat.setCurrentRow(i)
                return

    def activate(self):
        """Reiter wird sichtbar: im Katalog-Reiter hinzugefügte/entfernte Firmwares übernehmen (Auswahl bleibt)."""
        keys = [s.key for s in firmware.catalog() + firmware.STOCK]
        if keys == [self.cat.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.cat.count())]:
            return
        cur = self.current_spec()
        self._fill_catalog()
        if cur is not None and cur.key in keys and (self.current_spec() is None or self.current_spec().key != cur.key):
            self.cat.setCurrentRow(keys.index(cur.key))

    # -------------------------------------------------------------
    def _fill_catalog(self):
        self.cat.clear()
        for s in firmware.catalog() + firmware.STOCK:
            it = QListWidgetItem(icon("application-x-firmware", "cpu", "chip"), s.name)
            it.setData(Qt.ItemDataRole.UserRole, s.key)
            self.cat.addItem(it)
        key = self.ctx.firmware_key or "melodee"
        for i in range(self.cat.count()):
            if self.cat.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.cat.setCurrentRow(i)
                return
        self.cat.setCurrentRow(0)

    def current_spec(self) -> firmware.FirmwareSpec | None:
        it = self.cat.currentItem()
        return firmware.spec(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def _select(self, _row):
        s = self.current_spec()
        if not s:
            return
        self.desc.setText(f"<b>{s.name}</b> – {s.description}<br>Samples: {s.samples or 'unbekannt'} · "
                          f"<a href='{s.url}'>{s.repo}</a>")
        self.v16_warn.setVisible(s.key == "stock-v16" or s.product == "FM-1_016")
        self.b_res.setVisible(bool(s.resources))
        self.load_releases()

    def showEvent(self, e):
        super().showEvent(e)
        self._shown = True
        if getattr(self, "_pending_load", False):         # erst jetzt (Seite sichtbar) die Versionen holen
            self._pending_load = False
            self.load_releases()

    def load_releases(self, force=False):
        s = self.current_spec()
        if not s:
            return
        if not force and not getattr(self, "_shown", False):
            # Datenschutz: beim Programmstart nicht ungefragt ins Netz – erst, wenn du die Seite öffnest
            self._pending_load = True
            self._message_row("Versionen werden geladen, sobald du diese Seite öffnest.")
            return
        self.loading.add(s.key)
        self._message_row("lade …")

        def fetch(w):
            try:
                return firmware.fetch_releases(s, 0 if force else 600)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(firmware.friendly_error(e)) from e
        run_job(self, fetch, on_done=lambda rels: self._got(s.key, rels),
                on_fail=lambda m, tb: self._got(s.key, [], m))

    def _message_row(self, text: str):
        """eine umbrechende Hinweiszeile statt der Versionsliste (lädt, offline, keine Releases)"""
        self.tree.clear()
        self.notes.clear()
        it = QTreeWidgetItem([text])
        it.setFlags(Qt.ItemFlag.ItemIsEnabled)
        it.setToolTip(0, text)
        self.tree.addTopLevelItem(it)
        it.setFirstColumnSpanned(True)
        if hasattr(self, "b_install"):
            self._release_selected()

    def _got(self, key, rels, err=""):
        self.loading.discard(key)
        self.releases[key] = rels
        if err:
            firmware.LAST_ERROR[key] = err
        cur = self.current_spec()
        if cur and cur.key == key:
            self._fill()
            if err:
                self.notes.setPlainText(f"Releases konnten nicht geladen werden: {err}")
        if self.ctx.firmware_key == key:
            self.updates_checked.emit(self.update_status())

    def _fill(self):
        s = self.current_spec()
        self.tree.clear()
        if not s:
            return
        rels = self.releases.get(s.key, [])
        if not rels:
            if s.key in self.loading or s.key not in self.releases:
                self._message_row("lade …")
                return
            why = firmware.LAST_ERROR.get(s.key, "")
            self._message_row("Keine Versionen gefunden. " + (why or "Mögliche Gründe: kein Internet, das GitHub-"
                              "Abruf-Limit (60 pro Stunde) ist erreicht, oder das Projekt hat keine Releases.")
                              + " „Neu laden“ versucht es erneut.")
            return
        show_rc = self.chk_rc.isChecked()
        installed = self.ctx.firmware_tag if self.ctx.firmware_key == s.key else ""
        local = {p.parent.name for p in firmware.local_packages() if p.parent.parent.name == s.key}
        for r in rels:
            if r.prerelease and not show_rc and r.tag != installed:
                continue
            state = []
            if r.tag == installed:
                state.append("● installiert")
            if r.tag in local:
                state.append("heruntergeladen")
            it = QTreeWidgetItem([r.tag, _date_text(r.date), "Release Candidate" if r.prerelease else "stabil",
                                  ", ".join(state)])
            it.setData(0, Qt.ItemDataRole.UserRole, r.tag)
            if r.tag == installed:
                f = it.font(0)
                f.setBold(True)
                for c in range(4):
                    it.setFont(c, f)
            self.tree.addTopLevelItem(it)
        self.tree.setColumnHidden(1, not any(r.date for r in rels))   # ohne Datumsangaben keine leere Spalte
        for c in range(4):
            self.tree.resizeColumnToContents(c)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._release_selected()

    def _mark(self):
        if self.current_spec() and self.current_spec().key == self.ctx.firmware_key:
            self._fill()

    def selected_release(self) -> firmware.Release | None:
        s = self.current_spec()
        it = self.tree.currentItem()
        if not s or not it:
            return None
        tag = it.data(0, Qt.ItemDataRole.UserRole)
        return next((r for r in self.releases.get(s.key, []) if r.tag == tag), None)

    def _release_selected(self, *_):
        r = self.selected_release()
        if r:
            self.notes.setMarkdown(r.notes or f"_{r.tag}: keine Release-Notizen abrufbar._")
        s = self.current_spec()
        has = r is not None
        self.b_install.setEnabled(has and self.ctx.port is not None and not getattr(self.ctx, "is_virtual", False))
        self.b_dl.setEnabled(has)
        if not has:
            tip = "Erst eine Version in der Liste wählen." if s and self.releases.get(s.key) else \
                  "Keine Version geladen – „Neu laden“ versuchen (Internet nötig) oder eine eigene .fwsc-Datei wählen."
            self.b_install.setToolTip(tip)
            self.b_dl.setToolTip(tip)
        elif self.ctx.port is None:
            self.b_install.setToolTip("Erst ein FM-1 per USB anschließen – oder „Nur herunterladen“ für später.")
            self.b_dl.setToolTip(f"{s.name} {r.tag} nur in den Firmware-Ordner laden (installiert nichts)")
        else:
            self.b_install.setToolTip(f"{s.name} {r.tag} auf das aktive FM-1 installieren – mit Plan, Prüfsumme und "
                                      "Bestätigung")
            self.b_dl.setToolTip(f"{s.name} {r.tag} nur in den Firmware-Ordner laden (installiert nichts)")

    def update_status(self) -> dict:
        key = self.ctx.firmware_key
        s = firmware.spec(key)
        if not s:
            return {"error": "Firmware des Geräts unbekannt"} if key else {}
        rel = firmware.latest(self.releases.get(key, []), self.ctx.settings.channel)
        cur, new = self.ctx.firmware_tag, rel.tag if rel else ""
        if cur and new and new != cur:
            try:                               # Kanal „stabil“ mit installiertem RC: kein „Update“ auf eine ältere Version
                if firmware.version_key(new) <= firmware.version_key(cur):
                    new = cur
            except Exception:  # noqa: BLE001
                pass
        return {"firmware": key, "current": cur, "latest": new}

    def check_for_device(self):
        key = self.ctx.firmware_key
        s = firmware.spec(key)
        if not s:
            return
        run_job(self, lambda w: firmware.fetch_releases(s, 600), on_done=lambda rels: self._got(key, rels),
                on_fail=lambda m, tb: self.updates_checked.emit({"error": m}))

    # ------------------------------------------------------- Aktionen
    def _install(self):
        s, r = self.current_spec(), self.selected_release()
        if s and r:
            install_flow(self, self.ctx, s, r)

    def _download(self):
        s, r = self.current_spec(), self.selected_release()
        if not (s and r):
            return
        from . import feedback

        def said(pkg) -> str:
            check = ("Prüfsumme geprüft" if pkg.verified else
                     "Prüfsumme nicht verfügbar (das Release hat keine Prüfsummen-Datei)")
            return (f"{s.name} {r.tag} heruntergeladen ({pkg.fwsc.name}, {check}) – noch NICHT installiert. "
                    f"Gespeichert in {pkg.fwsc.parent}.")
        feedback.run(self, f"{s.name} {r.tag} herunterladen",
                     german_errors(lambda w: firmware.download(s, r, log=w.log.emit,
                                                               progress=lambda a, b: w.progress.emit(a, b))),
                     on_done=lambda _: self._fill(), done=said,
                     actions=[("Jetzt installieren …", lambda pkg: install_flow(self, self.ctx, s, r)),
                              feedback.open_folder_action(lambda pkg: pkg.fwsc.parent)])

    def _install_local(self, path: str = ""):
        """eigene .fwsc: Kennung + Prüfsumme zeigen, im Katalog suchen, dann den normalen Plan"""
        from ..flash import package as fpk
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Firmware-Paket wählen", str(self.ctx.settings.firmware_dir),
                                                  "FM-1-Firmware (*.fwsc)")
        if not path:
            return
        try:
            info = fpk.analyze(path)
        except fpk.PackageError as e:
            show_error(self, "Kein FM-1-Paket", str(e))
            return
        match = match_by_sha(info.sha256)          # nur über die Prüfsumme – nie über den Dateinamen
        text = (f"<b>{Path(path).name}</b><br>Kennung: <b>{info.product}</b><br>SHA-256: <tt>{info.sha256}</tt><br>"
                f"Loader-Familie: {dict(stock='Original (M-VAVE)', felucca='Felucca-Familie').get(info.family, 'unbekannt')}"
                + (f"<br>Beschädigt: {info.damage}" if info.damage else "") + "<br><br>"
                + (f"Erkannt als <b>{match.name}</b> (Prüfsumme stimmt mit Katalog bzw. geprüftem Release überein)."
                   if match else "Diese Prüfsumme steht in keinem Katalog und in keinem geprüften Release – die "
                   "Datei ist <b>unbekannt</b>, egal wie sie heißt. Du musst im Plan ausdrücklich bestätigen, dass "
                   "du weißt, was du tust."))
        if not confirm(self, "Eigene Firmware-Datei", text, "Weiter zum Plan"):
            return
        sp = match or firmware.FirmwareSpec(key="eigene", name=Path(path).stem, repo="", asset="", sums="", installer="",
                                            editor="", builtin=False, protocol="none", product=info.product,
                                            resources="")
        m = re.search(r"(\d+\.\d+(?:\.\d+)?(?:-rc\.\d+)?)", Path(path).name)
        tag = ("v" + m.group(1)) if m and sp.repo else (sp.release_tag or "")
        install_flow(self, self.ctx, sp, None, Path(path), tag)

    def return_to_original(self):
        """Zurück zur Original-Firmware – V15 (empfohlen) oder V16"""
        dlg = QDialog(self)
        dlg.setWindowTitle("Zurück zum Original")
        lay = QVBoxLayout(dlg)
        lab = QLabel("<b>V15</b> ist der bewährte Rückweg: alle Installer kennen sie, und fast alle Community-Firmwares "
                     "werden von ihr aus installiert. <b>V16</b> (Oktober 2026) ist M-VAVEs neueste Version mit acht "
                     "Stimmen und 64-Step-Pattern. M-VAVE tauscht die V16-Datei auf dem Server gelegentlich aus – "
                     "passt die Prüfsumme nicht, installiert die App nichts.<br><br>Danach am FM-1 einmal "
                     "<i>System &gt; Reset Patches</i>, falls Presets verstümmelt aussehen.")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        combo = QComboBox()
        combo.addItem("Original-Firmware V15 (empfohlen)", "stock-v15")
        combo.addItem("Original-Firmware V16 (experimentell – nur mit Backup)", "stock-v16")
        lay.addWidget(combo)
        warn = QLabel("<b>Achtung V16:</b> " + V16_WARNING_HTML)
        warn.setWordWrap(True)
        warn.setStyleSheet("QLabel { color: #da4453; }")
        warn.setVisible(False)
        combo.currentIndexChanged.connect(lambda _i: warn.setVisible(combo.currentData() == "stock-v16"))
        lay.addWidget(warn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        sp = firmware.spec(combo.currentData())
        return install_flow(self, self.ctx, sp, firmware.fetch_releases(sp)[0])

    def return_from_v16(self, key: str = "melodee", tag: str = ""):
        """geführter Rückweg V16 → V15 → Zielfirmware (der Plan erzwingt den Zwischenschritt über V15)"""
        dlg = QDialog(self)
        dlg.setWindowTitle("Rückweg von V16")
        lay = QVBoxLayout(dlg)
        lab = QLabel("<b>So läuft der Rückweg:</b><ol>"
                     "<li>Ein Backup geht nicht – V16 hat kein Editor-Protokoll. Was du unter V16 gespeichert hast, "
                     "ist danach weg.</li>"
                     "<li>Original-V15 aufspielen (gleicher Update-Loader wie V16).</li>"
                     "<li>Deine Zielfirmware aufspielen; bei Melodee danach Schrift und Werkssounds.</li>"
                     "<li>Dein Backup von vor V16 zurückspielen (Seite „Backups“).</li></ol>"
                     "Reste von V16 (0x93000–0xA8FFF) bleiben im Flash liegen; prüfen kannst du das in der Rettung mit "
                     "„Ist mein FM-1 sauber?“.")
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(lab)
        form = QFormLayout()
        fw = QComboBox()
        specs = [sp for sp in firmware.catalog() if sp.repo and sp.key != "eigene"]
        for sp in specs:
            fw.addItem(sp.name, sp.key)
        fw.addItem("Original-Firmware V15 (nur V15)", "stock-v15")
        i = fw.findData(key)
        fw.setCurrentIndex(max(0, i))
        ver = QComboBox()
        ver.setMinimumWidth(220)
        state = QLabel("")
        state.setProperty("role", "muted")
        form.addRow("Zielfirmware:", fw)
        form.addRow("Version:", ver)
        lay.addLayout(form)
        lay.addWidget(state)
        bb = QDialogButtonBox()
        ok = bb.addButton("Weiter zum Plan", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setProperty("variant", "primary")
        bb.addButton("Abbrechen", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        def fill(k):
            ver.clear()
            sp = firmware.spec(k)
            rels = self.releases.get(k)
            if rels is None:
                ok.setEnabled(False)
                state.setText("Versionen werden geladen …")
                run_job(dlg, lambda w: firmware.fetch_releases(sp, 600),
                        on_done=lambda r: (self.releases.__setitem__(k, r), fw.currentData() == k and fill(k)),
                        on_fail=lambda m, tb: state.setText(f"Versionen nicht ladbar: {m}"))
                return
            for r in rels:
                ver.addItem(r.tag + (" (Testversion)" if r.prerelease else ""), r.tag)
            prof = self.ctx.profile
            before = next((h.get("version", "") for h in reversed(list(getattr(prof, "firmware_history", []) or []))
                           if h.get("firmware") == k), "") if prof else ""
            lt = firmware.latest(rels, self.ctx.settings.channel) or (rels[0] if rels else None)
            want = tag or before or (lt.tag if lt else "")
            ver.setCurrentIndex(max(0, ver.findData(want)))
            ok.setEnabled(bool(rels))
            state.setText("" if rels else "Keine Version gefunden.")
        fw.currentIndexChanged.connect(lambda _i: fill(fw.currentData()))
        fill(fw.currentData())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        sp = firmware.spec(fw.currentData())
        rel = next((r for r in self.releases.get(sp.key, []) if r.tag == ver.currentData()), None)
        if sp is None or rel is None:
            show_error(self, "Keine Version gewählt", "Bitte eine Version wählen. Am FM-1 wurde nichts verändert.")
            return False
        return install_flow(self, self.ctx, sp, rel)

    def _show_history(self):
        p = self.ctx.profile
        hist = list(getattr(p, "firmware_history", []) or []) if p else []
        if not hist:
            self.history.setText("<small>Firmware-Verlauf dieses Geräts: noch keine Installation über die App.</small>")
            return
        rows = [f"{time.strftime('%d.%m.%Y', time.localtime(h.get('time', 0)))}: {h.get('name', h.get('firmware'))} {h.get('version', '')}"
                + (" (über V15)" if h.get("via_v15") else "") for h in hist[-5:]][::-1]
        self.history.setText("<small><b>Firmware-Verlauf:</b> " + " · ".join(rows) + "</small>")

    def open_with(self, install=None, file=None, **_):
        """Deep-Link: install=FirmwareSpec|key (z. B. aus dem Katalog), file=Pfad zu einer .fwsc"""
        sp = firmware.spec(install) if isinstance(install, str) else install
        if file:
            if sp is not None:
                return install_flow(self, self.ctx, sp, None, Path(file), sp.release_tag or "")
            return self._install_local(str(file))
        if sp is not None:
            for i in range(self.cat.count()):
                if self.cat.item(i).data(Qt.ItemDataRole.UserRole) == sp.key:
                    self.cat.setCurrentRow(i)
            if sp.download_url:
                return install_flow(self, self.ctx, sp, firmware.fetch_releases(sp)[0])
        return None

    def _resources(self):
        s = self.current_spec()
        tag = self.ctx.firmware_tag
        if getattr(self.ctx, "is_virtual", False):
            show_error(self, "Kein echtes FM-1", "Der Studio-Zwilling ist kein echtes FM-1 – Firmware wird nur auf ein angeschlossenes Gerät installiert. Bitte oben ein echtes FM-1 wählen.")
            return
        if not (s and s.resources and tag and self.ctx.port):
            show_error(self, "Nicht möglich", "Dafür muss ein FM-1 mit bekannter Melodee-Version verbunden sein.")
            return
        from .risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return
        init_gui_caller()                      # Rückfrage zum Werkzeug kommt aus dem Hintergrund-Job
        rel = next((r for r in self.releases.get(s.key, []) if r.tag == tag), None)
        port = self.ctx.port.in_name
        link = self.ctx.link

        if rel is None:
            show_error(self, "Version nicht in der Liste",
                       f"Die installierte Version {tag} steht nicht in der geladenen Versionsliste von {s.name}. "
                       "Bist du online? Bitte „Neu laden“ drücken und es dann erneut versuchen. Am FM-1 wurde nichts "
                       "verändert.")
            return

        def work(w):
            pkg = firmware.download(s, rel, log=w.log.emit)
            if not pkg.res:
                raise RuntimeError(f"{s.name} {tag} hat keine Ressourcen-Datei – es gibt nichts nachzuschreiben. "
                                   "Am FM-1 wurde nichts verändert.")
            repo = firmware.ensure_tools(s, tag, w.log.emit)
            try:
                firmware.check_tool(s, repo, tag)
            except firmware.ToolNotTrusted as e:       # erst fragen (im GUI-Thread), dann erneut starten
                if not ask(e):
                    raise RuntimeError("Abgebrochen: Das Werkzeug aus dem Repo wurde nicht ausgeführt. Am FM-1 "
                                       "wurde nichts verändert.") from None
                firmware.approve_tool(s, tag, e.commit)
            if link:
                link.close()
            return firmware.write_resources(s, repo, pkg.res, port, w.log.emit, tries=3, exact=True, tag=tag)

        def ask(e) -> bool:
            return call_in_gui(lambda: self.confirm_tool(e))

        def done(how):
            if how == "current":     # Melodee ab 1.6.0-rc.8: der Update-Loader hat sie schon geschrieben
                return (f"Die Ressourcen von {s.name} {tag} (Schriften, Werkssounds, Bluetooth-Programm) waren schon "
                        "vollständig – das FM-1 hat Länge und Prüfsumme selbst bestätigt, es wurde nichts geschrieben.")
            return (f"Ressourcen von {s.name} {tag} geschrieben (Schriften, Werkssounds, Bluetooth-Programm). "
                    "Die Firmware selbst wurde nicht verändert.")
        from . import feedback
        self.ctx.hold = True
        try:
            feedback.run(self, "Ressourcen schreiben", german_errors(work), done=done)
        finally:
            self.ctx.disconnect(None)
            self.ctx.reconnect_soon()

    def confirm_tool(self, e) -> bool:
        """Rückfrage, bevor Code aus einem Firmware-Repo läuft (fremde Firmware oder verschobener Tag)."""
        moved = (f"<br><br><b>Achtung:</b> Der Tag zeigte vorher auf {html.escape(e.moved_from[:12])}. Ein "
                 "verschobener Tag kann ein harmloses Neu-Veröffentlichen sein – oder veränderter Code."
                 if e.moved_from else "")
        return confirm(self, "Werkzeug aus dem Firmware-Repo ausführen?",
                       f"{html.escape(str(e))}<br><br>Repo: <b>github.com/{html.escape(e.repo)}</b><br>"
                       f"Tag: <b>{html.escape(e.tag or '?')}</b><br>Commit: <tt>{html.escape(e.commit)}</tt>{moved}"
                       "<br><br>Das Werkzeug ist ein Python-Programm aus diesem Repo und läuft mit deinen "
                       "Benutzerrechten. Nur erlauben, wenn du dem Repo vertraust. Die Zustimmung gilt für genau "
                       "diesen Commit.", "Ausführen", danger=True)

    def _add(self):
        try:                                   # Katalog-Werkstatt: Link-Import (Repo, Release oder .fwsc-Adresse)
            from . import linkimport_dialog
            dlg_cls = getattr(linkimport_dialog, "LinkImportDialog", None)
        except ImportError:
            dlg_cls = None
        if dlg_cls is not None:
            dlg = dlg_cls(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._fill_catalog()
                self.list_changed.emit()
                self.ctx.message.emit("Firmware hinzugefügt – sie steht jetzt in der Liste (noch nichts installiert).")
            return
        d = AddFirmwareDialog(self)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        e = d.entry()
        if not e:
            show_error(self, "Unvollständig", "Bitte Name, Repo (besitzer/name) und Paketname mit {ver} angeben.")
            return
        s = self.ctx.settings
        s.extra_firmwares = [x for x in s.extra_firmwares if x.get("key") != e["key"]] + [e]
        config.save(s)
        self._fill_catalog()
        self.list_changed.emit()
        self.ctx.message.emit(f"Firmware „{e['name']}“ hinzugefügt – noch nichts heruntergeladen oder installiert.")

    def _remove(self):
        sp = self.current_spec()
        if not sp or sp.builtin:
            show_error(self, "Nicht möglich", "Die eingebauten Firmwares lassen sich nicht entfernen.")
            return
        if not confirm(self, "Aus der Liste entfernen", f"„{sp.name}“ aus der Firmware-Liste entfernen?<br>"
                       "Heruntergeladene Pakete bleiben im Firmware-Ordner, am FM-1 ändert sich nichts.", "Entfernen"):
            return
        s = self.ctx.settings
        s.extra_firmwares = [x for x in s.extra_firmwares if x.get("key") != sp.key]
        config.save(s)
        self._fill_catalog()
        self.list_changed.emit()
        self.ctx.message.emit(f"„{sp.name}“ aus der Liste entfernt. Heruntergeladene Pakete bleiben im Firmware-Ordner, "
                              "am FM-1 ändert sich nichts.")


def _row(*ws):
    h = QHBoxLayout()
    for w in ws:
        if w is None:
            h.addStretch(1)
        else:
            h.addWidget(w)
    return h
