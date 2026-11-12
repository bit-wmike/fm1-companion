# SPDX-License-Identifier: GPL-3.0-only
"""Firmware: catalog, versions, install, switch, own packages."""
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
from ..i18n import _, pgettext
from .common import JobDialog, call_in_gui, confirm, flow_row, heading, icon, init_gui_caller, run_job, show_error
from .responsive import adaptive_splitter, fit_height, wrap_height


def find_backup_for(ctx, key: str) -> Path | None:
    """most recent backup of this device that was made with firmware `key`."""
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
    """FirmwareSpec with this checksum (built-in, custom, original, catalog, self-verified releases).
    Never via the file name – a renamed foreign file stays “unknown”."""
    sha = (sha or "").lower()
    for sp in firmware.catalog() + firmware.STOCK:
        if sp.sha256 and sp.sha256.lower() == sha:
            return sp
    pinned = firmware.spec_by_pinned_sha(sha)         # a version the app pins itself (KARMA)
    if pinned is not None:
        return pinned
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
    """is this file verifiably (checksum) the firmware `spec`?"""
    if spec.sha256 and info.sha256 == spec.sha256.lower():
        return True
    if info.official and info.official == spec.product:
        return True
    m = match_by_sha(info.sha256)
    return m is not None and m.key == spec.key


def device_count(ctx) -> int:
    """how many FM-1s (also in update mode) are currently connected – one device per USB port"""
    seen = set()
    for p in ctx.ports:
        seen.add(p.usb_path or p.key)
    return len(seen) + len(getattr(ctx, "uboot", []) or [])


def target_pin(ctx):
    """pin the active device via USB port (sysfs) or unique serial number – None if that isn't
    possible (then installing with several FM-1s is not allowed)"""
    from ..devices import usable_serial
    from ..flash.engine import Pin
    p = ctx.port
    if p is None or getattr(ctx, "is_virtual", False):      # studio twin: never a flash target
        return None
    if p.usb_path:
        return Pin(usb_path=p.usb_path)
    serial = usable_serial(p, ctx.ports or [p])
    return Pin(serial=serial) if serial else None


def device_state(ctx) -> "planner.DeviceState":
    from ..flash import journal, planner
    from .device import firmware_name
    return planner.DeviceState(identity=getattr(ctx, "identity", "") or (ctx.profile.identity if ctx.profile else ""),
                               felucca=bool(ctx.connected), firmware_key=ctx.firmware_key,
                               firmware_name=firmware_name(ctx.firmware_key) if ctx.firmware_key else "",
                               version=ctx.info.version if ctx.info else (ctx.profile.version_string if ctx.profile
                                                                          else ""),
                               port=ctx.port.in_name if ctx.port else "", connected=ctx.port is not None,
                               ports=device_count(ctx), busy=journal.Journal.busy(),
                               pinned=target_pin(ctx) is not None, usb_notes=usb_notes(ctx))


def usb_notes(ctx) -> list[str]:
    """installation plan: is the FM-1 behind a USB hub (careful mode), is USB power saving on?"""
    p = getattr(ctx, "port", None)
    if p is None or not getattr(p, "usb_path", "") or getattr(ctx, "is_virtual", False):
        return []
    try:
        from .. import devices
        from ..flash import robust
        return robust.plan_notes(devices.usb_link(p.usb_path), getattr(ctx.settings, "careful_usb", "auto"))
    except Exception:  # noqa: BLE001 – a note only
        return []


from ..flash.planner import V16_WARNING as V16_WARNING_HTML  # noqa: E402

RISK_COLOR = {"keins": "#27ae60", "niedrig": "#27ae60", "mittel": "#f67400", "hoch": "#da4453"}


class WrapCheck(QWidget):
    """Checkbox with wrapping text (QCheckBox itself doesn't wrap and cuts off)"""
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
        self.label.setMinimumWidth(1)              # may become narrower than the text – then it wraps
        # at the top next to the box, even if the layout gives more height (otherwise the text slips one line down)
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
    """Preview of the installation plan: steps, warnings, what gets lost, confirmations, dry run."""

    def __init__(self, parent, plan_fn, target_label: str, backup_default: bool, via_default: bool | None = None):
        super().__init__(parent)
        from ..flash import planner
        self.planner = planner
        self.plan_fn = plan_fn
        self.setWindowTitle(_("Installation plan"))
        self.setMinimumSize(820, 680)
        self.resize(880, 740)
        lay = QVBoxLayout(self)
        head = QLabel(f"<h3>{target_label}</h3>")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.view = QTextBrowser()
        lay.addWidget(self.view, 1)
        self.lost_label = QLabel()                 # “What will no longer be on the device afterwards” – always visible
        self.lost_label.setWordWrap(True)
        self.lost_label.setTextFormat(Qt.TextFormat.RichText)
        self.lost_label.setObjectName("PlanLost")
        self.lost_label.setStyleSheet("QLabel#PlanLost { border: 1px solid #f67400; border-radius: 6px; padding: 6px; }")
        self.lost_label.hide()
        lay.addWidget(self.lost_label)
        # policy: a firmware switch always goes via the original V15 first – the expert may opt out (not remembered)
        self.v15_note = QLabel()
        self.v15_note.setWordWrap(True)
        self.v15_note.setTextFormat(Qt.TextFormat.RichText)
        self.chk_direct = WrapCheck(_("Expert: install directly without the V15 step"))
        self.chk_direct.setToolTip(_("Skips the original V15 before the switch. Leftovers of the previous firmware can "
                                     "then stay in the flash. Only for experts – the choice is not remembered."))
        lay.addWidget(self.v15_note)
        lay.addWidget(self.chk_direct)
        self.chk_backup = WrapCheck(_("Make a backup first (recommended)"))
        self.chk_backup.setChecked(backup_default)
        lay.addWidget(self.chk_backup)
        self.backup_note = QLabel()
        self.backup_note.setWordWrap(True)
        lay.addWidget(self.backup_note)
        self.confirm_box = QVBoxLayout()
        lay.addLayout(self.confirm_box)
        self.chk_dry = WrapCheck(_("Dry run only: check everything, write nothing"))
        lay.addWidget(self.chk_dry)
        self.bb = QDialogButtonBox()
        self.ok_btn = self.bb.addButton(_("Install"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok_btn.setProperty("variant", "primary")
        self.bb.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        self.ok_hint = QLabel(_("Please confirm above"))
        self.ok_hint.setProperty("role", "muted")
        brow = QHBoxLayout()
        brow.addWidget(self.ok_hint)
        brow.addStretch(1)
        brow.addWidget(self.bb)
        lay.addLayout(brow)
        self.confirm_checks: dict[str, WrapCheck] = {}
        self._via = via_default                    # None = the policy, False = the expert opt-out
        self.plan = None
        self.refresh()
        self.chk_direct.toggled.connect(self._direct_toggled)
        self.chk_backup.toggled.connect(lambda _on: self.refresh())
        self.chk_dry.toggled.connect(lambda on: (self.ok_btn.setText(_("Start dry run") if on else _("Install")),
                                                 self._update_ok()))

    def _direct_toggled(self, on):
        self._via = False if on else None
        self.refresh()

    def _call_plan(self):
        try:
            return self.plan_fn(self._via, self.chk_backup.isChecked())
        except TypeError:                          # older callers: plan_fn(via)
            return self.plan_fn(self._via)

    def refresh(self):
        p = self.plan = self._call_plan()
        opt = p.v15_option
        self.chk_direct.blockSignals(True)
        self.chk_direct.box.blockSignals(True)
        self.chk_direct.setVisible(opt == "default_on")
        self.chk_direct.setChecked(p.direct)
        self.chk_direct.box.blockSignals(False)
        self.chk_direct.blockSignals(False)
        self.v15_note.setVisible(opt != "none")
        note = p.v15_reason or (_("Mandatory: according to the catalog, this firmware is only installed from the "
                                  "original V15.") if opt == "locked" else "")
        if p.direct:
            note = "<span style='color:#da4453'>" + _("Without the V15 step: leftovers of the previous firmware can "
                                                      "stay on the FM-1 (see Notes).") + "</span>"
        self.v15_note.setText(note)
        can_backup = p.state.felucca and not p.resume
        self.chk_backup.setVisible(can_backup)
        self.chk_backup.setText(_("Make a backup first (mandatory when switching with V16)")
                                if self.planner.layout_change(p.state, p.target) else
                                _("Make a backup first (recommended)"))
        self.backup_note.setVisible(not p.resume)
        self.backup_note.setText(
            ("" if self.chk_backup.isChecked() else
             _("Without backup: you have to confirm this explicitly below.")) if can_backup else
            _("Backup: the app cannot make a backup of the running firmware (no editor protocol). "
              "Back up important projects first with the firmware's own tool."))
        writes = " · " + _("writes")
        rows = "".join(
            f"<tr><td><b>{n}.</b></td><td><b>{st.title}</b><br><small>{st.text}</small></td>"
            f"<td style='color:{RISK_COLOR.get(st.risk, '#888')}'>{self.planner.RISK_TEXT.get(st.risk, st.risk)}"
            f"{writes if st.writes else ''}</td></tr>" for n, st in enumerate(p.steps, 1))
        html = f"<table cellspacing=6>{rows}</table>"
        if p.blockers:
            html += ("<h4 style='color:#da4453'>" + _("Not possible") + "</h4><ul>"
                     + "".join(f"<li>{b}</li>" for b in p.blockers) + "</ul>")
        if p.warnings:
            html += "<h4>" + _("Notes") + "</h4><ul>" + "".join(f"<li>{w}</li>" for w in p.warnings) + "</ul>"
        html += ("<p><small>" + _("While writing, do not unplug the USB cable and do not switch the FM-1 off.")
                 + "</small></p>")
        self.view.setHtml(html)
        self.lost_label.setVisible(bool(p.lost))
        if p.lost:
            self.lost_label.setText("<b>" + _("What is no longer on the device afterwards:") + "</b> " + " · ".join(p.lost))
        for c in list(self.confirm_checks):
            if c not in p.confirmations:
                cb = self.confirm_checks.pop(c)
                self.confirm_box.removeWidget(cb)
                cb.deleteLater()
        for c in p.confirmations:
            if c not in self.confirm_checks:
                cb = WrapCheck(self.planner.CONFIRM_TEXT.get(c, c), bold=True)
                cb.toggled.connect(lambda _on: self._update_ok())
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
        self.ok_hint.setText(_("Not possible – see above") if not self.plan.ok else _("Please confirm above"))


class InstallDialog(JobDialog):
    """Progress with log; cancel only as long as nothing has been sent yet. While the FM-1 writes in update mode:
    red notice “Don't unplug now”, cancel disabled."""

    CANCEL_TIP = _("Cancels before the next safe point. While writing, the installation runs to the end – "
                   "cancelling there would leave the FM-1 in update mode.")

    def __init__(self, parent, title, fn, cancel_event, **kw):
        self.cancel_event = cancel_event
        self.writing = False
        self.danger = QLabel(_("<b>Do not unplug and do not switch off now.</b> The FM-1 is writing its firmware. "
                               "This takes a few minutes – after that it restarts by itself."))
        self.danger.setObjectName("InstallDanger")
        self.danger.setWordWrap(True)
        self.danger.setStyleSheet("QLabel#InstallDanger { color: #ffffff; background: #da4453; border-radius: 6px; "
                                  "padding: 8px; }")
        self.danger.hide()
        super().__init__(parent, title, fn, **kw)
        self.layout().insertWidget(1, self.danger)
        self.btn_cancel = self.buttons.addButton(_("Cancel (only before writing)"),
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
            self.btn_cancel.setToolTip(_("While the FM-1 is writing, it cannot be cancelled safely – please wait.")
                                       if on else self.CANCEL_TIP)

    def _cancel(self):
        if self.writing:
            return
        self.cancel_event.set()
        self.add(_("Cancellation requested – takes effect at the next safe point."))
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
        # the cancel messages (cancel_text(), engine "cancelled") all start with this word
        cancelled = pgettext("install state", "Cancelled")
        if self.cancel_event.is_set() and str(a[0] if a else "").startswith(cancelled):
            self._state(False, cancelled, self.result_label.text())   # intended – not “failed”


def _install(*a, **kw):
    """firmware.install with the extra arguments the current version knows (tests partly replace it)"""
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
    """Error in the background job as a readable sentence; the technical details end up in the log"""
    def run(w, *a, **k):
        try:
            return work(w, *a, **k)
        except Exception as e:  # noqa: BLE001
            if type(e) in (RuntimeError, PackageRefused):      # already a finished, translated sentence
                raise
            w.log.emit(_("Technical details: {details}").format(details=firmware.explain(e)))
            raise RuntimeError(firmware.friendly_error(e)) from e
    return run


class PackageRefused(RuntimeError):
    pass


def validate_package(path, spec, known: bool, label: str, expect_sha: str = "", official: str = "", log=None):
    """fully check a package BEFORE anything is written: identifier, checksum, model, integrity,
    loader family and rule. -> PackageInfo; PackageRefused with a readable reason."""
    from ..flash import package as fpk, planner
    try:
        pi = fpk.analyze(path, spec.loader_family)
    except fpk.PackageError as e:
        raise PackageRefused(f"{label}: {e}")
    t = planner.Target.from_spec(spec)
    t.known = known
    probs = planner.check_package(t, pi)
    if pi.loader_rule is None and not any("Loader" in x or "loader" in x for x in probs):
        probs.append(_("The loader family of the package is unknown – it is not sent to any update loader."))
    if official and pi.official != official:
        probs.append(_("This is not the unmodified original firmware ({identity}).").format(identity=official))
    if expect_sha and pi.sha256 != expect_sha.lower():
        probs.append(_("The file has changed since the plan (different checksum)."))
    if log:
        line = _("{label}: {product}, SHA-256 {sha256}…, loader family {family}").format(
            label=label, product=pi.product, sha256=pi.sha256[:16], family=pi.family)
        if pi.damage:
            line = _("{line}, DAMAGED ({damage})").format(line=line, damage=pi.damage)
        if not probs:
            line = _("{line} – OK").format(line=line)
        log(line)
    if probs:
        raise PackageRefused(_("{label} is not installed: {problems}").format(label=label, problems=" ".join(probs)))
    return pi


def install_flow(parent, ctx, spec: firmware.FirmwareSpec, rel: firmware.Release | None = None,
                 local_pkg: Path | None = None, tag: str = "", dry_run: bool | None = None) -> bool:
    """complete flow: plan (preview, confirmations) → download and check ALL packages → backup → (V15) →
    install → resources → identity → update profile/history. Writing only starts once every
    package of the plan has been checked; the target device is pinned via its USB port."""
    import threading
    from ..flash import package as fpk, planner
    s = ctx.settings
    if getattr(ctx, "is_virtual", False):
        show_error(parent, _("Not a real FM-1"), _("The Studio twin is not a real FM-1 – firmware is only installed on a "
                                                   "connected device. Please choose a real FM-1 at the top."))
        return False
    if not ctx.port:
        show_error(parent, _("No device"), _("Please connect the FM-1 via USB."))
        return False
    from .risk_dialog import ensure_risk_ack
    if not ensure_risk_ack(parent):              # one-time risk notice before the first installation
        return False
    target = rel.tag if rel else (tag or _("own file"))
    info = None
    if local_pkg is not None:
        try:
            info = fpk.analyze(local_pkg, spec.loader_family)
        except fpk.PackageError as e:
            show_error(parent, _("No FM-1 package"), str(e))
            return False
    state = device_state(ctx)
    pin = target_pin(ctx)
    planned_port = ctx.port                      # the device the plan applies to
    tgt = planner.Target.from_spec(spec, tag=rel.tag if rel else tag, file=local_pkg, package=info,
                                   prerelease=bool(rel and rel.prerelease))
    if local_pkg is not None:
        # own file: known only if the checksum matches (catalog, release SHA256SUMS, original) – never by name
        tgt.known = file_matches(spec, info)
    else:
        tgt.checksum = bool(spec.sha256) or bool(rel and spec.sums and spec.sums in rel.assets)
    pinned = (rel.sha256 if rel else "") or firmware.pinned_sha(spec, rel.tag if rel else tag)
    if pinned and local_pkg is None:
        tgt.sha256, tgt.checksum = pinned, True        # versions pinned in the app (KARMA's site packages)
    from ..flash import journal as fj
    jr = fj.Journal()
    chain = jr.chain

    def make(via, backup=True):
        return planner.plan_install(state, tgt, via_v15=via, backup=backup, chain=chain)
    dlg = PlanDialog(parent, make, _("{firmware} on {device}").format(
                         firmware=firmware.display_label(spec, target if rel or tag else ''),
                         device=ctx.profile.name if ctx.profile else _("this FM-1")),
                     bool(s.auto_backup_before_update) and ctx.connected)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    plan = dlg.plan
    if not plan.ok:
        show_error(parent, _("Installation not possible"), "\n".join(re.sub("<[^>]+>", "", b) for b in plan.blockers))
        return False
    if dlg.missing():
        show_error(parent, _("Confirmation missing"), _("Please confirm all points in bold in the plan explicitly:")
                   + "\n\n" + "\n".join("• " + planner.CONFIRM_TEXT.get(c, c) for c in dlg.missing()))
        return False
    do_dry = dlg.chk_dry.isChecked() if dry_run is None else dry_run
    # while the plan was open, device detection kept running: is it still the same FM-1 in the same state?
    if ctx.port is None or planned_port is None or ctx.port.key != planned_port.key:
        show_error(parent, _("Device has changed"),
                   _("While the plan was open, the FM-1 was disconnected or another device became active. Nothing was "
                     "written. Please connect the desired FM-1 or select it at the top left, and open the plan again."))
        return False
    if "backup" in plan.kinds() and not do_dry and not ctx.connected:
        show_error(parent, _("Backup not possible"),
                   _("The plan includes a backup, but the connection to the FM-1 is gone in the meantime. Nothing was "
                     "written. Please reconnect and open the plan again."))
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
            from .device import FolderError
            show_error(parent, _("Backup folder not writable"),
                       str(e) if isinstance(e, FolderError)
                       else firmware.friendly_error(e))
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
            return _("Cancelled – nothing was changed on the FM-1.")
        return _("Cancelled after the intermediate step: {written} was written to the FM-1 – it now runs the "
                 "original firmware V15. {name} was NOT written. To continue: Firmware+ › Install & Versions › "
                 "“Continue: install {name}” (the V15 step is not repeated).").format(written=", ".join(written),
                                                                                     name=spec.name)

    def check_cancel():
        if cancel.is_set():
            raise RuntimeError(cancel_text())

    def work(w):
        # ---- 1. download and fully check every package of the plan – nothing on the device yet
        w.progress.emit(0, 0)
        w.log.emit(_("Step: load & check all packages (before any writing) …"))
        stock_spec = firmware.spec("stock-v15")
        stock_info = None
        try:
            if via_stock:
                st_pkg = firmware.download(stock_spec, firmware.fetch_releases(stock_spec)[0], log=w.log.emit)
                stock_info = validate_package(st_pkg.fwsc, stock_spec, True, _("Original V15"), official="FM-1_015",
                                              log=w.log.emit)
            if rel:
                pkg = firmware.download(spec, rel, log=w.log.emit, progress=lambda a, b: w.progress.emit(a, b))
                path, res, t = pkg.fwsc, pkg.res, rel.tag
                if not pkg.verified and not spec.sha256:
                    w.log.emit(_("Checksum not available: this release has no checksum file (confirmed in the plan)."))
            else:
                path, res, t = local_pkg, None, tag
                if spec.resources:
                    cand = list(path.parent.glob("*-res.bin"))
                    res = cand[0] if cand else None
            tgt_info = validate_package(path, spec, known, firmware.display_label(spec, t), expect_sha=local_sha,
                                        log=w.log.emit)
        except PackageRefused:
            raise
        except Exception as e:  # noqa: BLE001 – network/file: before any writing, so we say so too
            w.log.emit(_("Technical details: {details}").format(details=firmware.explain(e)))
            raise RuntimeError(_("{error} Nothing has been changed on the FM-1.").format(error=firmware.friendly_error(e))) from e
        w.log.emit(_("All packages checked."))
        check_cancel()
        # ---- 2. Backup (read only). If it was requested and fails: always abort.
        if do_backup:
            w.log.emit(_("Step: backup of the device …"))
            if link is None or link.closed:
                raise RuntimeError(_("Backup not possible: the connection to the FM-1 is gone. Installation aborted – "
                                     "nothing was changed on the FM-1."))
            try:
                from .. import autobackup
                with link.exclusive():
                    bpath = autobackup.capture_to_file(
                        link, bk_folder, profile, old_fw, info_version,
                        note=_("automatically before installing {label}").format(label=label), kind="vor-Update",
                        progress=lambda a, b: w.progress.emit(a, b))
                w.log.emit(_("Backup saved: {name}").format(name=bpath.name))
            except Exception as e:  # noqa: BLE001
                w.log.emit(_("Technical details: {details}").format(details=firmware.explain(e)))
                raise RuntimeError(_("Backup failed – installation aborted, nothing was changed on the FM-1. "
                                     "Reason: {error} If you want to install without a backup, untick “Make a backup "
                                     "first” in the plan and confirm that.").format(error=firmware.friendly_error(e)))
        check_cancel()
        if link and not do_dry:
            link.close()
        # ---- 3. Dry run
        if do_dry:
            from ..flash import engine as fe
            e = fe.Engine(log=w.log.emit)
            e.write(stock_info or tgt_info, port=port_name, dry_run=True, pin=pin)
            w.log.emit(_("Dry run finished – nothing was written."))
            return {"dry": True}
        check_cancel()
        # ---- 4. Write
        if stock_info is not None:
            # two-step switch: the journal remembers the target, so an interruption after V15 continues with it
            jr.begin_chain(key=spec.key, name=spec.name, tag=t or "", file=str(local_pkg) if local_pkg else "",
                           device_id=profile.id if profile else "")
            w.log.emit(_("Step: intermediate step original V15 …"))
            w.progress.emit(0, 100)
            try:
                _install(stock_spec, stock_info.path, "V15", port_name=port_name, log=w.log.emit,
                         progress=lambda pct: w.progress.emit(pct, 100),
                         device_id=profile.id if profile else "", cancel=cancel, pin=pin, expect_sha=stock_info.sha256)
            except Exception as e:  # noqa: BLE001
                if getattr(e, "state", "safe") == "safe":
                    jr.clear_chain()           # nothing written, the old firmware keeps running: no chain to continue
                raise
            written.append(_("the original firmware V15"))
            w.log.emit(_("Original V15 is running. Continuing with the target firmware …"))
            check_cancel()
        w.log.emit(_("Step: install {name} …").format(name=spec.name))
        w.progress.emit(0, 100)
        report: dict = {}
        try:
            ident = _install(spec, tgt_info.path, t, port_name=port_name, res=res, log=w.log.emit,
                             progress=lambda pct: w.progress.emit(pct, 100),
                             device_id=profile.id if profile else "", confirmed_unknown=True,
                             cancel=cancel, pin=pin, expect_sha=tgt_info.sha256, report=report)
        except Exception as e:  # noqa: BLE001
            if written:
                w.log.emit(_("Technical details: {details}").format(details=firmware.explain(e)))
                if getattr(e, "code", "") == "cancelled" or cancel.is_set():
                    raise RuntimeError(cancel_text()) from e
                st = getattr(e, "state", "safe")
                if st == "paused":
                    now = _("The FM-1 is now waiting in update mode – {name} is not completely written. "
                            "Do not unplug anything: continue on the “Recovery” page with “Finish installation”.").format(
                                name=spec.name)
                elif st == "restart":
                    now = _("{name} was written, but the FM-1 did not report as expected afterwards. "
                            "Switch it off and on again and check which firmware it shows.").format(name=spec.name)
                else:
                    now = _("The FM-1 now runs the original firmware V15; {name} was not written.").format(name=spec.name)
                raise RuntimeError(_("{error} Written before: {written}. {now}").format(
                    error=firmware.friendly_error(e), written=", ".join(written), now=now)) from e
            raise
        w.log.emit(_("Device now reports: {identity}").format(identity=ident or _("unknown")))
        jr.clear_chain()                       # the target is written: no two-step switch is pending any more
        if spec.protocol == "felucca" and spec.repo and spec.editor:
            try:
                editor_host.prepare(spec, t, log=w.log.emit)
            except Exception as e:  # noqa: BLE001
                w.log.emit(_("The editor will be provided later ({error})").format(error=firmware.friendly_error(e)))
        return {"ident": ident, "tag": t, "resources": report.get("resources", ""), "backup": bool(do_backup),
                "via": bool(stock_info is not None)}

    def summary(res) -> str:
        if res.get("dry"):
            return _("Dry run successful – all packages checked, the FM-1 answered. Nothing was changed on the "
                     "FM-1.")
        parts = [_("Installation finished – the FM-1 now reports {identity} ({label})").format(
            identity=res.get('ident') or _("an unknown identity"), label=label)]
        if res.get("via"):
            parts.append(_("via the original V15"))
        if res.get("resources"):
            parts.append(res["resources"])
        parts.append(_("backup saved beforehand") if res.get("backup") else _("without backup"))
        text = ", ".join(parts) + "."
        if spec.key.startswith("stock") or via_stock:
            text += " " + _("The app checked the checksum of the package and the identity after the restart "
                            "({identity}). Whether every byte in the flash is right can only be checked with a flash "
                            "dump (Recovery › “Is my FM-1 clean?”).").format(identity=res.get('ident') or '?')
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
        title = (_("{label} check (dry run)") if do_dry else _("{label} install")).format(label=label)
        d = feedback.run(parent, title, german_errors(work),
                         cancel, dialog_cls=InstallDialog, on_done=done, done=summary)
    finally:
        ctx.disconnect(None)
        ctx.reconnect_soon(2500)      # also releases ctx.hold
    return d.ok


class AddFirmwareDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Add firmware"))
        f = QFormLayout(self)
        self.name = QLineEdit()
        self.repo = QLineEdit()
        self.repo.setPlaceholderText(_("owner/repo on GitHub, e.g. charlesvestal/fm1-x0x"))
        self.asset = QLineEdit()
        # Translators: {ver} is typed literally by the user – keep it
        self.asset.setPlaceholderText(_("Package name in the release, {{ver}} = version, e.g. x0x-{{ver}}.fwsc").format())
        self.res = QLineEdit()
        self.res.setPlaceholderText(_("optional: resource file, e.g. name-{{ver}}-res.bin").format())
        self.desc = QLineEdit()
        f.addRow(_("Name:"), self.name)
        f.addRow(_("GitHub repo:"), self.repo)
        f.addRow(_("Package:"), self.asset)
        f.addRow(_("Resources:"), self.res)
        f.addRow(_("Description:"), self.desc)
        hint = QLabel(_("Every FM-1 firmware can be installed: the app uses the built-in installer and checks the "
                        "package identity and update loader. Backups, projects and editor are only available for "
                        "firmwares of the Felucca family with an editor protocol."))
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


# Translators: strftime format of a date (day.month.year in German)
DATE_FORMAT = pgettext("date format", "%Y-%m-%d")


def _date_text(iso: str) -> str:
    """Release date for display (DD.MM.YYYY), "–" if unknown."""
    if not iso:
        return "–"
    try:
        import datetime as _dt
        return _dt.date.fromisoformat(iso[:10]).strftime(DATE_FORMAT)
    except ValueError:
        return iso


class FirmwarePage(QWidget):
    """Install & versions – tab of the “Firmware+” page (formerly its own “Firmware” page; the page key
    "firmware" redirects to this tab)."""
    updates_checked = pyqtSignal(dict)
    list_changed = pyqtSignal()                # firmware added/removed – the catalog tab follows

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.releases: dict[str, list[firmware.Release]] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("Install & Versions"), _("Check versions, update or switch to another firmware. "
                                                         "Every FM-1 firmware: with installation plan, checksum, dry "
                                                         "run and the matching update loader.")))
        split = QSplitter()
        lay.addWidget(split, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.cat = QListWidget()
        self.cat.currentRowChanged.connect(self._select)
        ll.addWidget(self.cat, 1)
        b_add = QPushButton(icon("list-add"), _("Add firmware …"))
        b_add.clicked.connect(self._add)
        b_del = QPushButton(icon("list-remove"), _("Remove"))
        b_del.clicked.connect(self._remove)
        ll.addLayout(_row(b_add, b_del))
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setOpenExternalLinks(True)
        wrap_height(self.desc)
        rl.addWidget(self.desc)
        self.v16_warn = QLabel(_("<b>Caution V16:</b> {warning}").format(warning=V16_WARNING_HTML))
        self.v16_warn.setWordWrap(True)
        self.v16_warn.setStyleSheet("QLabel { color: #da4453; border: 1px solid #da4453; border-radius: 4px; "
                                    "padding: 6px; }")
        self.v16_warn.setVisible(False)
        rl.addWidget(self.v16_warn)
        # device runs V16: guided way back (via V15, then the target firmware and for Melodee the resources)
        self.v16_back = QWidget()
        self.v16_back.setObjectName("V16Back")
        vb = QVBoxLayout(self.v16_back)
        vb.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(_("<b>This FM-1 runs V16 (experimental).</b> The way back goes via the original V15: the app "
                       "first installs V15, then your target firmware and, with Melodee, the font and factory sounds "
                       "after that."))
        lab.setWordWrap(True)
        vb.addWidget(lab)
        self.b_v16_back = QPushButton(icon("edit-undo", "go-previous"), _("Way back from V16 …"))
        self.b_v16_back.setProperty("variant", "primary")
        self.b_v16_back.setToolTip(_("Choose target firmware and version – then the installation plan shows every step"))
        self.b_v16_back.clicked.connect(lambda: self.return_from_v16())
        vb.addWidget(flow_row(self.b_v16_back))
        self.v16_back.setVisible(False)
        rl.addWidget(self.v16_back)
        # a switch that stopped after its V15 step (journal "chain"): continue with the target
        self.chain_box = QWidget()
        self.chain_box.setObjectName("ChainResume")
        cb = QVBoxLayout(self.chain_box)
        cb.setContentsMargins(0, 0, 0, 0)
        self.chain_label = QLabel()
        self.chain_label.setWordWrap(True)
        cb.addWidget(self.chain_label)
        self.b_chain = QPushButton(icon("go-next", "media-playback-start"), _("Continue the switch …"))
        self.b_chain.setProperty("variant", "primary")
        self.b_chain.clicked.connect(self.continue_chain)
        cb.addWidget(flow_row(self.b_chain))
        self.chain_box.setVisible(False)
        rl.addWidget(self.chain_box)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Version"), _("Date"), pgettext("release list", "Type"), pgettext("release list", "Status")])
        self.tree.setRootIsDecorated(False)
        self.tree.setWordWrap(True)
        self.loading: set[str] = set()
        self.tree.currentItemChanged.connect(self._release_selected)
        self.tree.itemDoubleClicked.connect(lambda *_a: self._install())
        rl.addWidget(self.tree, 2)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        rl.addWidget(self.notes, 3)
        self.b_install = QPushButton(icon("system-software-update", "go-down"), _("Install on active FM-1"))
        self.b_install.setProperty("variant", "primary")
        self.b_install.clicked.connect(self._install)
        self.b_dl = QPushButton(icon("download", "go-down"), _("Download only"))
        self.b_dl.clicked.connect(self._download)
        self.b_refresh = QPushButton(icon("view-refresh"), _("Reload"))
        self.b_refresh.clicked.connect(lambda: self.load_releases(force=True))
        self.chk_rc = QCheckBox(_("Show release candidates"))
        self.chk_rc.setChecked(ctx.settings.channel == "rc")
        self.chk_rc.toggled.connect(self._fill)
        rl.addWidget(fit_height(flow_row(self.b_install, self.b_dl, self.b_refresh, self.chk_rc)))     # wraps when narrow
        self.b_local = QPushButton(icon("document-open"), _("Install own .fwsc file …"))
        self.b_local.clicked.connect(self._install_local)
        # flashmodel.py (pinned, German) names this button „Schrift & Werkssounds nachschreiben“; flash/texts.py
        # shows that hint in the UI language with this label
        self.b_res = QPushButton(icon("run-build", "system-run"), _("Rewrite font && factory sounds"))
        self.b_res.setToolTip(_("Melodee 1.6 and later: write the resource file (font, factory sounds, Bluetooth "
                                "program) to the FM-1 afterwards – e.g. if the display stays empty after the update"))
        self.b_res.clicked.connect(self._resources)
        self.b_stock = QPushButton(icon("edit-undo", "go-previous"), _("Back to the original …"))
        self.b_stock.setToolTip(_("Install M-VAVE's original firmware V15 or V16 (the checksum is always checked)"))
        self.b_stock.clicked.connect(self.return_to_original)
        rl.addWidget(fit_height(flow_row(self.b_local, self.b_stock, self.b_res)))
        self.history = QLabel()
        self.history.setWordWrap(True)
        self.history.setTextFormat(Qt.TextFormat.RichText)
        rl.addWidget(self.history)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        adaptive_splitter(split)                 # narrow: firmware list above the details

        self._fill_catalog()
        self._last_fw_key = self.ctx.firmware_key
        ctx.changed.connect(self._follow_device)
        ctx.changed.connect(lambda: self.v16_back.setVisible(getattr(self.ctx, "identity", "") == "FM-1_016"))
        ctx.changed.connect(self._mark)
        ctx.changed.connect(self._show_history)
        ctx.changed.connect(self._show_chain)
        ctx.changed.connect(self._release_selected)
        self._show_history()
        self._show_chain()
        self._release_selected()

    def _follow_device(self):
        """different device with different firmware active: select its catalog entry (versions, status)."""
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
        """Tab becomes visible: take over firmwares added/removed in the catalog tab (selection stays)."""
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
        self.desc.setText(f"<b>{s.name}</b> – {s.description}<br>"
                          + _("Samples: {samples}").format(samples=s.samples or _("unknown"))
                          + f" · <a href='{s.url}'>{s.repo}</a>")
        self.v16_warn.setVisible(s.key == "stock-v16" or s.product == "FM-1_016")
        self.b_res.setVisible(bool(s.resources))
        self.load_releases()

    def showEvent(self, e):
        super().showEvent(e)
        self._shown = True
        if getattr(self, "_pending_load", False):         # only now (page visible) fetch the versions
            self._pending_load = False
            self.load_releases()

    def load_releases(self, force=False):
        s = self.current_spec()
        if not s:
            return
        if not force and not getattr(self, "_shown", False):
            # privacy: don't go online unasked at program start – only when you open the page
            self._pending_load = True
            self._message_row(_("Versions are loaded as soon as you open this page."))
            return
        self.loading.add(s.key)
        self._message_row(_("loading …"))

        def fetch(w):
            try:
                return firmware.fetch_releases(s, 0 if force else 600)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(firmware.friendly_error(e)) from e
        run_job(self, fetch, on_done=lambda rels: self._got(s.key, rels),
                on_fail=lambda m, tb: self._got(s.key, [], m))

    def _message_row(self, text: str):
        """a wrapping notice line instead of the version list (loading, offline, no releases)"""
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
                self.notes.setPlainText(_("Releases could not be loaded: {error}").format(error=err))
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
                self._message_row(_("loading …"))
                return
            why = firmware.LAST_ERROR.get(s.key, "") or _("Possible reasons: no internet, the GitHub request limit "
                                                          "(60 per hour) has been reached, or the project has no "
                                                          "releases.")
            self._message_row(_("No versions found. {reason} “Reload” tries again.").format(reason=why))
            return
        show_rc = self.chk_rc.isChecked()
        installed = self.ctx.firmware_tag if self.ctx.firmware_key == s.key else ""
        local = {p.parent.name for p in firmware.local_packages() if p.parent.parent.name == s.key}
        for r in rels:
            if r.prerelease and not show_rc and r.tag != installed:
                continue
            state = []
            if r.tag == installed:
                state.append(_("● installed"))
            if r.tag in local:
                state.append(pgettext("release list", "downloaded"))
            it = QTreeWidgetItem([r.tag, _date_text(r.date), _("Release candidate") if r.prerelease else _("stable"),
                                  ", ".join(state)])
            it.setData(0, Qt.ItemDataRole.UserRole, r.tag)
            if r.tag == installed:
                f = it.font(0)
                f.setBold(True)
                for c in range(4):
                    it.setFont(c, f)
            self.tree.addTopLevelItem(it)
        self.tree.setColumnHidden(1, not any(r.date for r in rels))   # no empty column without dates
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

    def _release_selected(self, *_args):
        r = self.selected_release()
        if r:
            self.notes.setMarkdown(r.notes or "_" + _("{tag}: no release notes available.").format(tag=r.tag) + "_")
        s = self.current_spec()
        has = r is not None
        self.b_install.setEnabled(has and self.ctx.port is not None and not getattr(self.ctx, "is_virtual", False))
        self.b_dl.setEnabled(has)
        if not has:
            tip = _("First choose a version in the list.") if s and self.releases.get(s.key) else \
                  _("No version loaded – try “Reload” (internet needed) or choose your own .fwsc file.")
            self.b_install.setToolTip(tip)
            self.b_dl.setToolTip(tip)
        elif self.ctx.port is None:
            self.b_install.setToolTip(_("First connect an FM-1 via USB – or “Download only” for later."))
            self.b_dl.setToolTip(_("Only download {name} {tag} into the firmware folder (installs nothing)").format(
                name=s.name, tag=r.tag))
        else:
            self.b_install.setToolTip(_("Install {name} {tag} on the active FM-1 – with plan, checksum and "
                                        "confirmation").format(name=s.name, tag=r.tag))
            self.b_dl.setToolTip(_("Only download {name} {tag} into the firmware folder (installs nothing)").format(
                name=s.name, tag=r.tag))

    def update_status(self) -> dict:
        key = self.ctx.firmware_key
        s = firmware.spec(key)
        if not s:
            return {"error": _("Firmware of the device unknown")} if key else {}
        rel = firmware.latest(self.releases.get(key, []), self.ctx.settings.channel)
        cur, new = self.ctx.firmware_tag, rel.tag if rel else ""
        if cur and new and new != cur:
            try:                               # “stable” channel with an installed RC: no “update” to an older version
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

    # ------------------------------------------------------- Actions
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
            check = (_("checksum checked") if pkg.verified else
                     _("checksum not available (the release has no checksum file)"))
            return _("{name} {tag} downloaded ({file}, {check}) – NOT installed yet. Saved in {folder}.").format(
                name=s.name, tag=r.tag, file=pkg.fwsc.name, check=check, folder=pkg.fwsc.parent)
        feedback.run(self, _("Download {name} {tag}").format(name=s.name, tag=r.tag),
                     german_errors(lambda w: firmware.download(s, r, log=w.log.emit,
                                                               progress=lambda a, b: w.progress.emit(a, b))),
                     on_done=lambda _pkg: self._fill(), done=said,
                     actions=[(_("Install now …"), lambda pkg: install_flow(self, self.ctx, s, r)),
                              feedback.open_folder_action(lambda pkg: pkg.fwsc.parent)])

    def _install_local(self, path: str = ""):
        """own .fwsc: show identifier + checksum, search in the catalog, then the normal plan"""
        from ..flash import package as fpk
        if not path:
            path, _filter = QFileDialog.getOpenFileName(self, _("Choose firmware package"),
                                                        str(self.ctx.settings.firmware_dir), _("FM-1 firmware (*.fwsc)"))
        if not path:
            return
        try:
            info = fpk.analyze(path)
        except fpk.PackageError as e:
            show_error(self, _("No FM-1 package"), str(e))
            return
        match = match_by_sha(info.sha256)          # only via the checksum – never via the file name
        fam = {"stock": _("Original (M-VAVE)"), "felucca": _("Felucca family")}.get(info.family, _("unknown"))
        text = (f"<b>{Path(path).name}</b><br>" + _("Identity: <b>{product}</b>").format(product=info.product)
                + f"<br>SHA-256: <tt>{info.sha256}</tt><br>" + _("Loader family: {family}").format(family=fam)
                + ("<br>" + _("Damaged: {damage}").format(damage=info.damage) if info.damage else "") + "<br><br>"
                + (_("Recognized as <b>{name}</b> (checksum matches the catalog or a checked release).").format(
                    name=match.name)
                   if match else _("This checksum is in no catalog and in no checked release – the file is "
                                   "<b>unknown</b>, no matter what it is called. You must confirm explicitly in the "
                                   "plan that you know what you are doing.")))
        if not confirm(self, _("Own firmware file"), text, _("On to the plan")):
            return
        sp = match or firmware.FirmwareSpec(key="eigene", name=Path(path).stem, repo="", asset="", sums="", installer="",
                                            editor="", builtin=False, protocol="none", product=info.product,
                                            resources="")
        m = re.search(r"(\d+\.\d+(?:\.\d+)?(?:-rc\.\d+)?)", Path(path).name)
        tag = ("v" + m.group(1)) if m and sp.repo else (sp.release_tag or "")
        install_flow(self, self.ctx, sp, None, Path(path), tag)

    def return_to_original(self):
        """Back to the original firmware – V15 (recommended) or V16"""
        dlg = QDialog(self)
        dlg.setWindowTitle(_("Back to the original"))
        lay = QVBoxLayout(dlg)
        lab = QLabel(_("<b>V15</b> is the proven way back: all installers know it, and almost all community firmwares "
                       "are installed from it. <b>V16</b> (October 2026) is M-VAVE's newest version with eight "
                       "voices and 64-step patterns. M-VAVE occasionally replaces the V16 file on the server – "
                       "if the checksum does not match, the app installs nothing.<br><br>Afterwards, on the FM-1, run "
                       "<i>System &gt; Reset Patches</i> once if presets look garbled."))
        lab.setWordWrap(True)
        lay.addWidget(lab)
        combo = QComboBox()
        combo.addItem(_("Original firmware V15 (recommended)"), "stock-v15")
        combo.addItem(_("Original firmware V16 (experimental – only with a backup)"), "stock-v16")
        lay.addWidget(combo)
        warn = QLabel(_("<b>Caution V16:</b> {warning}").format(warning=V16_WARNING_HTML))
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
        """guided way back V16 → V15 → target firmware (the plan enforces the intermediate step via V15)"""
        dlg = QDialog(self)
        dlg.setWindowTitle(_("Way back from V16"))
        lay = QVBoxLayout(dlg)
        lab = QLabel(_("<b>This is how the way back works:</b><ol>"
                       "<li>A backup is not possible – V16 has no editor protocol. What you saved under V16 is gone "
                       "afterwards.</li>"
                       "<li>Install the original V15 (same update loader as V16).</li>"
                       "<li>Install your target firmware; with Melodee, the font and factory sounds after that.</li>"
                       "<li>Restore your backup from before V16 (“Backups” page).</li></ol>"
                       "Leftovers of V16 (0x93000–0xA8FFF) remain in the flash; you can check that in Recovery with "
                       "“Is my FM-1 clean?”."))
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(lab)
        form = QFormLayout()
        fw = QComboBox()
        specs = [sp for sp in firmware.catalog() if sp.repo and sp.key != "eigene"]
        for sp in specs:
            fw.addItem(sp.name, sp.key)
        fw.addItem(_("Original firmware V15 (V15 only)"), "stock-v15")
        i = fw.findData(key)
        fw.setCurrentIndex(max(0, i))
        ver = QComboBox()
        ver.setMinimumWidth(220)
        state = QLabel("")
        state.setProperty("role", "muted")
        form.addRow(_("Target firmware:"), fw)
        form.addRow(_("Version:"), ver)
        lay.addLayout(form)
        lay.addWidget(state)
        bb = QDialogButtonBox()
        ok = bb.addButton(_("On to the plan"), QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setProperty("variant", "primary")
        bb.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        def fill(k):
            ver.clear()
            sp = firmware.spec(k)
            rels = self.releases.get(k)
            if rels is None:
                ok.setEnabled(False)
                state.setText(_("Loading versions …"))
                run_job(dlg, lambda w: firmware.fetch_releases(sp, 600),
                        on_done=lambda r: (self.releases.__setitem__(k, r), fw.currentData() == k and fill(k)),
                        on_fail=lambda m, tb: state.setText(_("Versions cannot be loaded: {error}").format(error=m)))
                return
            for r in rels:
                ver.addItem(_("{tag} (test version)").format(tag=r.tag) if r.prerelease else r.tag, r.tag)
            prof = self.ctx.profile
            before = next((h.get("version", "") for h in reversed(list(getattr(prof, "firmware_history", []) or []))
                           if h.get("firmware") == k), "") if prof else ""
            lt = firmware.latest(rels, self.ctx.settings.channel) or (rels[0] if rels else None)
            want = tag or before or (lt.tag if lt else "")
            ver.setCurrentIndex(max(0, ver.findData(want)))
            ok.setEnabled(bool(rels))
            state.setText("" if rels else _("No version found."))
        fw.currentIndexChanged.connect(lambda _i: fill(fw.currentData()))
        fill(fw.currentData())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        sp = firmware.spec(fw.currentData())
        rel = next((r for r in self.releases.get(sp.key, []) if r.tag == ver.currentData()), None)
        if sp is None or rel is None:
            show_error(self, _("No version chosen"), _("Please choose a version. Nothing was changed on the FM-1."))
            return False
        return install_flow(self, self.ctx, sp, rel)

    def pending_chain(self) -> dict | None:
        """the journal's two-step switch whose V15 step is done – only while this FM-1 runs the original V15"""
        from ..flash import journal as fj
        try:
            ch = fj.Journal().chain
        except Exception:  # noqa: BLE001
            return None
        c = self.ctx
        if not ch or getattr(c, "is_virtual", False) or getattr(c, "identity", "") != "FM-1_015":
            return None
        if ch.get("device_id") and c.profile and c.profile.id and ch["device_id"] != c.profile.id:
            return None
        return ch

    def _show_chain(self):
        ch = self.pending_chain()
        self.chain_box.setVisible(ch is not None)
        if ch:
            name = firmware.display_label(firmware.spec(ch["key"]) or firmware.FirmwareSpec(
                key=ch["key"], name=ch.get("name") or ch["key"], repo="", asset=""), ch.get("tag", ""))
            self.chain_label.setText(_("<b>The switch to {name} stopped after its first step.</b> This FM-1 now runs "
                                       "the original firmware V15 – the second step installs {name} directly.").format(
                                           name=html.escape(name)))
            self.b_chain.setText(_("Continue: install {name} …").format(name=name))

    def continue_chain(self):
        """second step of an interrupted switch: the target, directly from V15 (the plan shows it)"""
        ch = self.pending_chain()
        if not ch:
            return False
        sp = firmware.spec(ch["key"])
        f = ch.get("file") or ""
        if f and Path(f).exists():
            if sp is not None:
                return install_flow(self, self.ctx, sp, None, Path(f), ch.get("tag", ""))
            return self._install_local(f)
        if sp is None:
            show_error(self, _("Firmware not found"), _("{name} is no longer in the list of firmwares. Please choose "
                                                        "it again – the FM-1 runs the original V15, every firmware can "
                                                        "be installed directly from it.").format(
                                                            name=ch.get("name") or ch["key"]))
            return False
        try:
            rels = firmware.fetch_releases(sp)
        except Exception as e:  # noqa: BLE001
            show_error(self, _("Versions cannot be loaded"), firmware.friendly_error(e))
            return False
        rel = next((r for r in rels if r.tag == ch.get("tag")), None) or (rels[0] if rels else None)
        if rel is None:
            show_error(self, _("No version found"), _("No version of {name} can be loaded right now.").format(
                name=sp.name))
            return False
        return install_flow(self, self.ctx, sp, rel)

    def _show_history(self):
        p = self.ctx.profile
        hist = list(getattr(p, "firmware_history", []) or []) if p else []
        if not hist:
            self.history.setText("<small>" + _("Firmware history of this device: no installation via the app yet.")
                                 + "</small>")
            return
        via = " " + _("(via V15)")
        rows = [f"{time.strftime(DATE_FORMAT, time.localtime(h.get('time', 0)))}: {h.get('name', h.get('firmware'))} {h.get('version', '')}"
                + (via if h.get("via_v15") else "") for h in hist[-5:]][::-1]
        self.history.setText("<small><b>" + _("Firmware history:") + "</b> " + " · ".join(rows) + "</small>")

    def open_with(self, install=None, file=None, **_kw):
        """Deep link: install=FirmwareSpec|key (e.g. from the catalog), file=path to a .fwsc"""
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
            show_error(self, _("Not a real FM-1"), _("The Studio twin is not a real FM-1 – firmware is only installed on a "
                                                     "connected device. Please choose a real FM-1 at the top."))
            return
        if not (s and s.resources and tag and self.ctx.port):
            show_error(self, _("Not possible"), _("For this, an FM-1 with a known Melodee version must be connected."))
            return
        from .risk_dialog import ensure_risk_ack
        if not ensure_risk_ack(self):
            return
        init_gui_caller()                      # the prompt about the tool comes from the background job
        rel = next((r for r in self.releases.get(s.key, []) if r.tag == tag), None)
        port = self.ctx.port.in_name
        link = self.ctx.link

        if rel is None:
            show_error(self, _("Version not in the list"),
                       _("The installed version {tag} is not in the loaded version list of {name}. Are you online? "
                         "Please press “Reload” and then try again. Nothing was changed on the FM-1.").format(
                           tag=tag, name=s.name))
            return

        def work(w):
            pkg = firmware.download(s, rel, log=w.log.emit)
            if not pkg.res:
                raise RuntimeError(_("{name} {tag} has no resource file – there is nothing to rewrite. "
                                     "Nothing was changed on the FM-1.").format(name=s.name, tag=tag))
            repo = firmware.ensure_tools(s, tag, w.log.emit)
            try:
                firmware.check_tool(s, repo, tag)
            except firmware.ToolNotTrusted as e:       # ask first (in the GUI thread), then start again
                if not ask(e):
                    raise RuntimeError(_("Cancelled: the tool from the repo was not run. Nothing was changed on "
                                         "the FM-1.")) from None
                firmware.approve_tool(s, tag, e.commit)
            if link:
                link.close()
            return firmware.write_resources(s, repo, pkg.res, port, w.log.emit, tries=3, exact=True, tag=tag)

        def ask(e) -> bool:
            return call_in_gui(lambda: self.confirm_tool(e))

        def done(how):
            if how == "current":     # Melodee from 1.6.0-rc.8: the update loader has already written them
                return _("The resources of {name} {tag} (fonts, factory sounds, Bluetooth program) were already "
                         "complete – the FM-1 confirmed length and checksum itself, nothing was written.").format(
                             name=s.name, tag=tag)
            return _("Resources of {name} {tag} written (fonts, factory sounds, Bluetooth program). "
                     "The firmware itself was not changed.").format(name=s.name, tag=tag)
        from . import feedback
        self.ctx.hold = True
        try:
            feedback.run(self, _("Write resources"), german_errors(work), done=done)
        finally:
            self.ctx.disconnect(None)
            self.ctx.reconnect_soon()

    def confirm_tool(self, e) -> bool:
        """Prompt before code from a firmware repo runs (foreign firmware or moved tag)."""
        moved = ("<br><br>" + _("<b>Caution:</b> the tag previously pointed to {commit}. A moved tag can be a "
                                "harmless re-release – or modified code.").format(commit=html.escape(e.moved_from[:12]))
                 if e.moved_from else "")
        return confirm(self, _("Run tool from the firmware repo?"),
                       f"{html.escape(str(e))}<br><br>Repo: <b>github.com/{html.escape(e.repo)}</b><br>"
                       f"Tag: <b>{html.escape(e.tag or '?')}</b><br>Commit: <tt>{html.escape(e.commit)}</tt>{moved}"
                       "<br><br>" + _("The tool is a Python program from this repo and runs with your user rights. "
                                      "Only allow it if you trust the repo. The approval applies to exactly this "
                                      "commit."), _("Run"), danger=True)

    def _add(self):
        try:                                   # catalog workshop: link import (repo, release or .fwsc address)
            from . import linkimport_dialog
            dlg_cls = getattr(linkimport_dialog, "LinkImportDialog", None)
        except ImportError:
            dlg_cls = None
        if dlg_cls is not None:
            dlg = dlg_cls(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._fill_catalog()
                self.list_changed.emit()
                self.ctx.message.emit(_("Firmware added – it is now in the list (nothing installed yet)."))
            return
        d = AddFirmwareDialog(self)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        e = d.entry()
        if not e:
            show_error(self, _("Incomplete"), _("Please enter name, repo (owner/name) and package name with {{ver}}.").format())
            return
        s = self.ctx.settings
        s.extra_firmwares = [x for x in s.extra_firmwares if x.get("key") != e["key"]] + [e]
        config.save(s)
        self._fill_catalog()
        self.list_changed.emit()
        self.ctx.message.emit(_("Firmware “{name}” added – nothing downloaded or installed yet.").format(name=e['name']))

    def _remove(self):
        sp = self.current_spec()
        if not sp or sp.builtin:
            show_error(self, _("Not possible"), _("The built-in firmwares cannot be removed."))
            return
        if not confirm(self, _("Remove from the list"), _("Remove “{name}” from the firmware list?<br>Downloaded "
                                                         "packages stay in the firmware folder, nothing changes on the "
                                                         "FM-1.").format(name=sp.name), _("Remove")):
            return
        s = self.ctx.settings
        s.extra_firmwares = [x for x in s.extra_firmwares if x.get("key") != sp.key]
        config.save(s)
        self._fill_catalog()
        self.list_changed.emit()
        self.ctx.message.emit(_("“{name}” removed from the list. Downloaded packages stay in the firmware folder, "
                                "nothing changes on the FM-1.").format(name=sp.name))


def _row(*ws):
    h = QHBoxLayout()
    for w in ws:
        if w is None:
            h.addStretch(1)
        else:
            h.addWidget(w)
    return h
