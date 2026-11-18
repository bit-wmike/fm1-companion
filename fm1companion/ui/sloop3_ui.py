# SPDX-License-Identifier: GPL-3.0-only
"""SLOOP 3 in the app's pages: the Firmware+ entry (what it needs, the way onto the FM-1, a backup of the whole
flash in m-bootloader's MIDI MODE) and restoring a SLOOP 3 backup.

The app does NOT install SLOOP 3 and does NOT install m-bootloader: SLOOP 3 goes onto the FM-1 from its author's
installer page (in the system's browser), through m-bootloader's MIDI MODE; m-bootloader itself with M-Flasher or
M-VAVE's M-UPGRADE. What the app does here only reads (flash/mboot.py) – except restoring a SLOOP 3 backup, which
writes what SLOOP 3's own editor writes (sloop3bk.py).
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from .. import sloop3 as S3
from ..i18n import _, ngettext
from .common import confirm, flow_row, fmt_date, fmt_size, icon, show_error

MBOOT_RELEASES = S3.MBOOT_URL + "/releases/latest"


# ------------------------------------------------------------- the hardware ---

def revision(ctx) -> tuple[str, str]:
    """-> ("new" | "old" | "unknown", where the answer comes from: "chip" = read from the flash chip in MIDI MODE,
    "profile" = what is recorded for this device (hardware.py), "" = nothing). The strictest reading wins: "new"
    from either source is "new"; a chip ID that was read and is neither of the two known ones is "unknown" – it
    never loses to a recorded "old"."""
    from .. import hardware
    mb = getattr(ctx, "mboot", None)
    prof = S3.revision_of(getattr(ctx, "profile", None))
    if mb is not None:
        chip = hardware.revision_of_id(mb.jedec & 0xFFFFFF)
        if chip == hardware.NEW or prof != hardware.NEW:
            return chip, "chip"
    return prof, ("profile" if prof != hardware.UNKNOWN else "")


def midi_mode_state(ctx, port=None) -> str:
    """is this update-mode port m-bootloader's MIDI MODE and not an FM-1 update loader? "yes" (it answered 0x12 with
    MBOOT), "no" (its name, USB ID, device version or handshake answer rule it out – flash/mboot.is_candidate), or
    "unclear": it looks exactly like MIDI MODE but did not answer the question. The active device: what was read
    when it connected, asked once more if that gave nothing; another port: asked now."""
    from ..flash import mboot
    port = port or getattr(ctx, "port", None)
    if port is None or not getattr(port, "update_mode", False):
        return "no"
    active = getattr(ctx, "port", None) is port
    if active and getattr(ctx, "mboot", None) is not None:
        return "yes"
    if not mboot.port_candidate(port, getattr(ctx, "identity", "") if active else ""):
        return "no"
    return "yes" if mboot.identify_port(port) is not None else "unclear"


def refuse_in_midi_mode(parent, ctx, port=None) -> bool:
    """the app's installer must not treat m-bootloader's MIDI MODE as an FM-1 update loader: it answers the same
    handshake and has the same USB ID, but writes a package from 0x4000 to the package's end – not a loader's
    application range – and so over what SLOOP 3 keeps there. True: it is MIDI MODE, or cannot be told apart from
    it; the reason was shown, nothing is sent."""
    st = midi_mode_state(ctx, port)
    if st == "no":
        return False
    if st == "unclear":
        show_error(parent, _("Cannot tell what is waiting here"),
                   _("This FM-1 shows up as “FM-1 Update” – the name of m-bootloader's MIDI MODE –, but it did not "
                     "answer the question who it is. The app cannot tell it apart from MIDI MODE and sends it no "
                     "firmware. Nothing was written.<br><br>If the FM-1 shows m-bootloader's menu or MIDI MODE: "
                     "firmware is installed from there with the installer page of that firmware's author. Otherwise "
                     "unplug the FM-1, switch it off and on, and try again."))
        return True
    show_error(parent, _("m-bootloader's MIDI MODE, not an update mode"),
               _("This FM-1 waits in m-bootloader's MIDI MODE. That is not the update mode of an FM-1 firmware: "
                 "m-bootloader answers the same way, but it writes a package differently, and the app has never "
                 "been tried with it. The app sends it no firmware. Nothing was written.<br><br>From MIDI MODE, "
                 "firmware is installed with the installer page of that firmware's author; the whole flash goes back "
                 "with M-Flasher. OCT−, OCT+, PLAY or REC on the FM-1 leave MIDI MODE. “Firmware+” › SLOOP 3 "
                 "explains more."))
    return True


def chip_plan(ctx):
    """the newer-FM-1 part of an installation plan for SLOOP 3 on this device – the app's one rule for it
    (flash/planner.chip_check with hardware.json's rows; blockers for a newer unit, the confirmation "no_logo" when
    the revision is not known)"""
    from .. import firmware
    from ..flash import planner
    rev, _src = revision(ctx)
    t = planner.Target.from_spec(firmware.spec(S3.KEY), tag="v" + S3.CHECKED_VERSIONS[-1])
    return planner.chip_check(rev if rev in ("new", "old") else "", t)


def revision_text(ctx) -> tuple[str, str]:
    """-> (level "bad" | "warn" | "ok", the sentence about this FM-1's hardware revision and SLOOP 3)"""
    from .. import hardware
    rev, src = revision(ctx)
    mb = getattr(ctx, "mboot", None)
    how = (_("Its flash chip says so itself ({chip}).").format(chip=mb.chip) if src == "chip" and mb is not None
           else _("That is what the app knows about this device.") if src else "")
    plan = chip_plan(ctx)
    m_boot = hardware.support("m-bootloader").status
    if plan.blockers or (rev == "new" and m_boot != hardware.YES):
        return "bad", " ".join(plan.blockers) + " " + how + " " + _(
            "<b>Do not install m-bootloader or SLOOP 3 on it for now.</b> The app does not offer the installer page "
            "for this device.")
    if "no_logo" in plan.confirmations or (rev != "old" and m_boot != hardware.YES):
        return "warn", " ".join(plan.warnings) + " " + _(
            "Sure sign: M-Flasher shows the FLASH CHIP – <code>0x856014</code> is the earlier one, "
            "<code>0xB36014</code> the newer.")
    return "ok", _("This FM-1 is an earlier unit (flash chip Puya P25Q80H), the one m-bootloader and SLOOP 3 were "
                   "tried on by their authors. {how}").format(how=how)


# --------------------------------------------------------- Firmware+ entry ---

def notes_html(ctx) -> str:
    """what the Firmware+ page shows for the SLOOP 3 entry (instead of release notes)"""
    level, rev = revision_text(ctx)
    color = {"bad": "#da4453", "warn": "#f67400", "ok": "#27ae60"}[level]
    a = lambda url, text: f'<a href="{html.escape(url)}">{html.escape(text)}</a>'   # noqa: E731
    return (
        "<h3>" + _("SLOOP 3 – public beta") + "</h3>"
        "<p>" + _("A rewrite of SLOOP from scratch by isod89: five tracks (four synths and the drums), 13 engines, "
                  "8 patterns per track, a song, samples and slices, its own web editor. Its author calls it a beta: "
                  "expect bugs, keep backups, do not rely on it on stage yet.") + "</p>"
        f"<p style='border-left:4px solid {color};padding-left:8px'>{rev}</p>"
        "<p><b>" + _("What it needs") + "</b></p><ul>"
        "<li>" + _("<b>m-bootloader</b> on the FM-1 (an open-source bootloader by Julius Vitkauskas). SLOOP 3 installs "
                   "only through its MIDI MODE. Installing m-bootloader replaces the FM-1's boot area – the app never "
                   "does that; it is done once with M-Flasher (USB download mode) or, from M-VAVE's firmware, with "
                   "M-VAVE's M-UPGRADE and the file fm1-uboot.fwsc.") + "</li>"
        "<li>" + _("<b>A backup of the whole flash first</b> (1 MB), made with M-Flasher or in MIDI MODE. It is the "
                   "only way back: SLOOP 2.8 or M-VAVE's firmware return by writing that backup back with M-Flasher.")
        + "</li>"
        "<li>" + _("An official M-VAVE update puts M-VAVE's own bootloader back; m-bootloader then has to be "
                   "installed again.") + "</li></ul>"
        "<p><b>" + _("What the app does – and what it does not") + "</b></p><ul>"
        "<li>" + _("It recognises an FM-1 that runs SLOOP 3 and one that waits in m-bootloader's MIDI MODE.") + "</li>"
        "<li>" + _("It shows SLOOP 3's own web editor on the page “Editor” and makes backups of its project, sounds "
                   "and banks on the page “Backups” (not of its samples: SLOOP 3 cannot hand them out).") + "</li>"
        "<li>" + _("In MIDI MODE it can copy the whole flash into your backup folder – reading only.") + "</li>"
        "<li>" + _("<b>It does not install SLOOP 3 and it does not install m-bootloader.</b> The button below shows "
                   "the way; for an FM-1 the rule above lets through, it opens the author's installer page in your "
                   "browser (Chromium or Chrome).")
        + "</li></ul>"
        "<p>" + _("Checked with SLOOP 3 {versions} on the author's PC twin (the firmware's code on a simulated FM-1). "
                  "<b>Never run on a real FM-1 by this app.</b>").format(versions=", ".join(S3.CHECKED_VERSIONS))
        + "</p><p>" + " · ".join(
            [a(S3.GUIDE_URL, _("Guide"))] +
            # (the installer page only where the hardware rule has no objection; else the dialog decides)
            ([a(S3.INSTALLER_URL, _("Installer page"))] if level == "ok" else []) +
            [a(S3.EDITOR_URL, _("Editor online")), a(S3.REPO, _("Project")), a(S3.MBOOT_URL, "m-bootloader"),
             a(S3.MFLASHER_URL, "M-Flasher")]) + "</p>")


class Sloop3Dialog(QDialog):
    """“SLOOP 3 – the way onto the FM-1”: the steps, this device's revision, the links, the flash backup"""

    def __init__(self, parent, ctx):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(_("SLOOP 3 (public beta) – the way onto the FM-1"))
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        self.level = "bad"                       # (until the first reading: nothing is offered)
        self.rev = QLabel("")
        self.rev.setObjectName("sloop3_revision")
        self.rev.setWordWrap(True)
        self.rev.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.rev)
        steps = QLabel(
            "<ol>"
            "<li>" + _("<b>Check for m-bootloader:</b> switch the FM-1 off, hold PLAY and switch it on. A menu with "
                       "BOOT APP, DOWNLOAD MODE and MIDI MODE? Then it is there.") + "</li>"
            "<li>" + _("<b>If not: install m-bootloader – outside this app.</b> With M-Flasher: USB download mode, "
                       "<b>back up the whole flash</b>, then install fm1-uboot.boot. Or, on M-VAVE's firmware, "
                       "fm1-uboot.fwsc with M-VAVE's M-UPGRADE. Keep the power on while the boot area is written.")
            + "</li>"
            "<li>" + _("<b>MIDI MODE:</b> hold PLAY at power-on, choose MIDI MODE with OCT−/OCT+, press PLAY, plug in "
                       "the USB cable. The app then shows “m-bootloader – MIDI MODE”.") + "</li>"
            "<li>" + _("<b>Back up the whole flash</b> (button below, or with M-Flasher) – unless you already have "
                       "such a backup of exactly this FM-1.") + "</li>"
            "<li>" + _("<b>Install SLOOP 3 with the author's installer page</b> in Chromium or Chrome (about a "
                       "minute). Close this app's connection first: quit the app or unplug and replug – only one "
                       "program can talk to the FM-1 in MIDI MODE at a time.") + "</li>"
            "<li>" + _("<b>Back to SLOOP 2.8 or M-VAVE's firmware:</b> write the flash backup back with M-Flasher "
                       "(hold REC at power-on for download mode). Save your SLOOP 3 projects as files first.")
            + "</li></ol>")
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(steps)
        honest = QLabel(_("None of this was run on a real FM-1 by this app. The app only reads here; installing is "
                          "done by the author's page and by M-Flasher."))
        honest.setWordWrap(True)
        honest.setProperty("role", "muted")
        lay.addWidget(honest)
        self.chk = QCheckBox(_("My FM-1 has no M-VAVE logo on the front (an earlier unit)"))
        self.chk.setObjectName("sloop3_confirm_old")
        self.chk.toggled.connect(lambda _on: self._update())
        lay.addWidget(self.chk)
        self.b_installer = QPushButton(icon("internet-web-browser", "applications-internet"),
                                       _("Open the installer page in the browser"))
        self.b_installer.setObjectName("sloop3_open_installer")
        self.b_installer.clicked.connect(lambda: self._open(S3.INSTALLER_URL))
        self.b_backup = QPushButton(icon("document-save"), _("Back up the whole flash now (MIDI MODE) …"))
        self.b_backup.setObjectName("sloop3_flash_backup")
        self.b_backup.clicked.connect(lambda: self._backup())
        b_guide = QPushButton(_("Guide"))
        b_guide.clicked.connect(lambda: self._open(S3.GUIDE_URL))
        b_mboot = QPushButton(_("m-bootloader (download)"))
        b_mboot.clicked.connect(lambda: self._open(MBOOT_RELEASES))
        b_mflasher = QPushButton("M-Flasher")
        b_mflasher.clicked.connect(lambda: self._open(S3.MFLASHER_URL))
        lay.addWidget(flow_row(self.b_installer, self.b_backup, b_guide, b_mboot, b_mflasher))
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("role", "muted")
        lay.addWidget(self.hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self._verdict()
        # the verdict is live: MIDI MODE appearing, the flash chip being read, another device – all arrive here
        self._on_ctx = lambda: self._verdict()
        ctx.changed.connect(self._on_ctx)
        self.finished.connect(lambda _r: self._unhook())

    def _unhook(self):
        try:
            self.ctx.changed.disconnect(self._on_ctx)
        except (TypeError, RuntimeError):
            pass

    def _verdict(self):
        """read the hardware rule for the device as it is now. A changed verdict takes the confirmation back."""
        try:
            level, text = revision_text(self.ctx)
        except Exception:  # noqa: BLE001 – no verdict is "not offered", never "fine"
            level, text = "bad", _("The app could not work out this FM-1's hardware revision. The installer page is "
                                   "not offered.")
        if level != self.level and self.chk.isChecked():
            self.chk.blockSignals(True)
            self.chk.setChecked(False)
            self.chk.blockSignals(False)
        self.level = level
        self.rev.setText(text)
        color = {"bad": "#da4453", "warn": "#f67400", "ok": "#27ae60"}[level]
        self.rev.setStyleSheet(f"border-left: 4px solid {color}; padding-left: 8px;")
        self.chk.setVisible(level == "warn")
        self._update()

    def installer_allowed(self) -> bool:
        """the app offers the installer page only for an FM-1 that is not the newer revision – and, where it cannot
        tell, only after the user said so"""
        return self.level == "ok" or (self.level == "warn" and self.chk.isChecked())

    def _update(self):
        ok = self.installer_allowed()
        self.b_installer.setEnabled(ok)
        self.b_installer.setToolTip(
            _("Opens {url} in your browser. The app itself installs nothing.").format(url=S3.INSTALLER_URL) if ok else
            _("Not offered for the newer FM-1 (logo on the front).") if self.level == "bad" else
            _("First confirm above which FM-1 this is."))
        in_midi = getattr(self.ctx, "mboot", None) is not None
        self.b_backup.setEnabled(in_midi)
        self.b_backup.setToolTip(
            _("Copies the whole flash (1 MB) into your backup folder and compares it with the checksum the FM-1's "
              "flash chip computes. Reading only.") if in_midi else
            _("Only in m-bootloader's MIDI MODE (step 3): the app must see “m-bootloader – MIDI MODE”."))
        self.hint.setText("" if in_midi else
                          _("The flash backup becomes available as soon as an FM-1 in MIDI MODE is the active "
                            "device."))

    def _open(self, url: str):
        from . import web
        if url == S3.INSTALLER_URL:
            self._verdict()                      # (once more, at the click: the device may have changed)
            if not self.installer_allowed():
                return
        web.open_external(url)

    def _backup(self):
        flash_backup(self, self.ctx)


def open_dialog(parent, ctx):
    d = Sloop3Dialog(parent, ctx)
    d.exec()
    return d


# ------------------------------------------------- flash backup (MIDI MODE) ---

def flash_backup(parent, ctx, opener=None) -> Path | None:
    """copy the whole flash of the FM-1 in m-bootloader's MIDI MODE into the device's backup folder (flash/mboot.py:
    reading only, checked against the chip's own CRC-32). -> the file, None if it did not happen."""
    from ..flash import mboot
    from . import feedback
    port, prof = ctx.port, ctx.profile
    if port is None or getattr(ctx, "mboot", None) is None:
        show_error(parent, _("Flash backup not possible"),
                   _("This works only while the FM-1 is in m-bootloader's MIDI MODE: hold PLAY at power-on, choose "
                     "MIDI MODE, plug in the USB cable."))
        return None
    try:
        folder = ctx.device_folder(ctx.settings.backups_dir) / "Flash"
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        show_error(parent, _("Flash backup not possible"),
                   _("The backup folder cannot be created: {error}").format(error=getattr(e, "strerror", None) or e))
        return None
    device = prof.name if prof else "FM-1"
    result: dict = {}

    def work(w):
        w.log.emit(_("Asking who answers …"))
        t = (opener or mboot.open_port)(port)
        try:
            host = mboot.Host(t)
            info = host.identify()
            if info is None:
                raise mboot.MbootError(_("This is not m-bootloader's MIDI MODE – nothing was read."))
            w.log.emit(_("m-bootloader {version}, flash chip {chip}, {size}").format(
                version=info.version, chip=info.chip, size=fmt_size(info.capacity)))
            w.log.emit(_("Reading the whole flash (nothing is written) …"))
            image = host.read_flash(progress=lambda a, b: w.progress.emit(a, b),
                                    abort=lambda: bool(getattr(w, "cancelled", False)))
            w.log.emit(_("The copy matches the checksum the flash chip computed."))
            path = mboot.save_backup(folder, device, info, image)
            mboot.check_backup(path)
            w.log.emit(_("File: {path}").format(path=path))
            result["path"] = path
            return path
        finally:
            try:
                t.close()
            except Exception:  # noqa: BLE001
                pass

    def said(path) -> str:
        return _("Flash backup saved and checked: {name} ({size}). Nothing was changed on the FM-1. It is the flash of "
                 "this one FM-1 – M-Flasher writes it back; keep a copy somewhere else too.").format(
                     name=Path(path).name, size=fmt_size(Path(path).stat().st_size))
    was_hold = ctx.hold
    ctx.hold = True                                  # (no device search while the port is in use)
    try:
        feedback.run(parent, _("Flash backup (MIDI MODE)"), work, done=said,
                     actions=[feedback.open_folder_action(lambda p: Path(p).parent)], cancellable=True)
    finally:
        ctx.hold = was_hold
    return result.get("path")


# ------------------------------------------------------- restoring a backup ---

def _format_of(path: Path) -> str:
    try:
        f = json.loads(Path(path).read_text("utf-8"))
        return f.get("format", "") if isinstance(f, dict) and isinstance(f.get("format"), str) else ""
    except (OSError, ValueError, RecursionError, UnicodeDecodeError):
        return ""


def handles_restore(parent, ctx, path: Path) -> bool:
    """the Backups page's “Restore”: True = this was a SLOOP 3 matter and is dealt with here (restored, refused or
    cancelled) – the Felucca family's restore, which would ask the FM-1 its commands, must not run."""
    from .. import backup
    is_s3_file = _format_of(path) == backup.SLOOP3_FORMAT
    s3 = getattr(ctx, "sloop3", None) if getattr(ctx, "midi_connected", False) else None
    if not is_s3_file and s3 is None:
        return False
    if is_s3_file and s3 is None:
        show_error(parent, _("Restore not possible"),
                   _("This is a SLOOP 3 backup. It goes back only onto an FM-1 that runs SLOOP 3 and is connected. "
                     "Nothing was written."))
        return True
    if not is_s3_file:
        show_error(parent, _("Restore not possible"), backup.sloop3_only_text())
        return True
    restore_flow(parent, ctx, path)
    return True


def restore_flow(parent, ctx, path: Path):
    from .. import backup, sloop3bk as B
    from .backups_page import make_backup
    try:
        a = backup.load_file(path)
        plan = B.restore_plan(a.file)
    except (backup.BackupError, B.Sloop3BackupError) as e:
        show_error(parent, _("Backup damaged"), str(e))
        return
    if not plan:
        show_error(parent, _("Restore not possible"), _("This backup holds nothing the app can write back."))
        return
    parts = "".join(f"<li>{html.escape(B.title(i))}</li>" for i in plan)
    name = html.escape(ctx.profile.name) if ctx.profile else "FM-1"
    skipped = [B.title(i) for i in (B.FM6, B.SYN) if i not in plan]
    if not confirm(parent, _("Restore SLOOP 3 backup"),
                   _("These parts on <b>{device}</b> are replaced by the state of {date}:").format(
                       device=name, date=fmt_date(a.created)) + f"<ul>{parts}</ul>" +
                   (_("Not written (the FM-1 had none of its own then): {parts}.").format(
                       parts=html.escape(", ".join(skipped))) + "<br>" if skipped else "") +
                   _("Your samples are not in the backup and stay as they are on the FM-1.") + "<br><br>" +
                   _("<b>Stop playback on the FM-1 first.</b> A safety backup of the current state is made "
                     "automatically. SLOOP 3 is a beta, and this restore was checked on its PC twin, never on a real "
                     "FM-1 by this app."), _("Restore"), danger=True):
        return
    safety = make_backup(ctx, parent, _("automatic, before restoring {name}").format(name=Path(path).name), "auto")
    if safety is None and not confirm(parent, _("No safety backup"),
                                      _("The safety backup failed. Restore anyway?"), _("Restore anyway"),
                                      danger=True):
        return
    state: dict = {"error": None}
    after = " " + (_("The previous state is in the safety backup {name}.").format(name=safety.name) if safety
                   else _("There is NO safety backup of the previous state."))

    def work(w, link):
        try:
            done = B.restore(link, a.file, progress=lambda x, y: w.progress.emit(x, y))
        except B.Sloop3BackupError as e:
            state["error"] = e
            for line in state_lines(e):
                w.log.emit(line)
            raise
        for i in done:
            w.log.emit(_("✓ {part}: written, but not compared (SLOOP 3 keeps it in its own layout)").format(
                part=B.title(i)) if i in done.unverified else
                _("✓ {part}: written, read back and identical").format(part=B.title(i)))
        return done

    def said(done) -> str:
        checked = [B.title(i) for i in done if i not in done.unverified]
        loose = [B.title(i) for i in done.unverified]
        text = ngettext("Restore complete: {n} part written.", "Restore complete: {n} parts written.",
                        len(done)).format(n=len(done))
        if checked:
            text += " " + _("Read back and identical: {parts}.").format(parts=html.escape(", ".join(checked)))
        if loose:
            text += " " + _("Written and accepted by the FM-1, but not compared: {parts}.").format(
                parts=html.escape(", ".join(loose)))
        return text + " " + _("Check the project on the FM-1.") + after

    def failed(m):
        e = state["error"]
        lines = state_lines(e) if e is not None else [_("Nothing was written.")]
        return _("Restore failed: {error}").format(error=m) + "<br>" + "<br>".join(html.escape(x) for x in lines) + after
    ctx.job(parent, _("Restore SLOOP 3 backup"), work, done=said, fail_text=failed)


def state_lines(e) -> list[str]:
    """after a restore that failed: what state each part is in, as far as the app knows (sloop3bk.restore)"""
    from .. import sloop3bk as B
    written, unclear = list(getattr(e, "written", None) or []), list(getattr(e, "unclear", None) or [])
    untouched = list(getattr(e, "untouched", None) or [])
    loose = set(getattr(e, "unverified", None) or [])
    out = []
    for i in written:
        out.append(_("✓ {part}: written, but not compared (SLOOP 3 keeps it in its own layout)").format(
            part=B.title(i)) if i in loose else _("✓ {part}: written, read back and identical").format(part=B.title(i)))
    for i in unclear:
        out.append(_("? {part}: this is where it stopped. It may be as before, as in the backup, or incomplete – "
                     "check it on the FM-1.").format(part=B.title(i)))
    for i in untouched:
        out.append(_("– {part}: not reached, nothing was sent for it.").format(part=B.title(i)))
    if not written and not unclear:
        out.insert(0, _("Nothing was written."))
    return out
