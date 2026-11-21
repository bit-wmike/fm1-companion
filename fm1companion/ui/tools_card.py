# SPDX-License-Identifier: GPL-3.0-only
"""Recovery › Tools: the tools the app bundles (M-Flasher, m-bootloader, SLOOP 3) – which version is bundled, which
one is in use, whether its checksum checks out, when the app last looked for a new version, "Check now",
"Update" (only when a newer version was found; never automatic) and "Show licence".

With M-Flasher the app also backs up the whole flash and writes such a backup back, puts the FM-1 into download
mode and restarts it, and installs or removes m-bootloader (ui/flash_tools.py, tools/flashjob.py); firmware through
download mode and SLOOP 3 come in later versions."""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QGridLayout, QLabel, QPlainTextEdit, QPushButton,
                             QVBoxLayout, QWidget)

from ..i18n import _
from ..tools import updates
from .common import Card, confirm, flow_row, icon, run_job, show_error


def _when(ts: float) -> str:
    if not ts:
        return _("never")
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def offer_text(o) -> str:
    return _("{tool} {version} is available ({date}).").format(
        tool=dict(updates.titles()).get(o.key, o.key), version=o.version, date=o.date or "?")


class LicenceDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Licence: {tool}").format(tool=title))
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit(text or _("No licence file found."))
        self.text.setReadOnly(True)
        lay.addWidget(self.text)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)


class ToolsCard(Card):
    """status view of the bundled tools"""

    def __init__(self, ctx, parent=None, store=None):
        super().__init__(_("Tools: M-Flasher, m-bootloader, SLOOP 3"), parent)
        self.setObjectName("toolsCard")
        self.ctx, self._store, self._busy, self._updating = ctx, store, False, False
        lay = self.layout()
        intro = QLabel(_("The app brings these tools with it and checks each file's checksum before it uses it. "
                         "With M-Flasher it backs up the whole flash of an FM-1 and writes such a backup back onto "
                         "the same unit, puts it into download mode and installs or removes m-bootloader (each write "
                         "only after a whole-flash backup); installing firmware through download mode and SLOOP 3 "
                         "follow in later versions. A new version is only shown – it is fetched when you click “Update”, checked "
                         "against GitHub's checksum, and the previous version stays as the fallback."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 4)
        lay.addWidget(self.grid_host)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setObjectName("toolsNote")
        lay.addWidget(self.note)
        self.b_check = QPushButton(icon("view-refresh"), _("Check now"))
        self.b_check.setObjectName("toolsCheckNow")
        self.b_check.setToolTip(_("Asks GitHub now for new versions of the three tools (also when automatic checks "
                                  "are switched off in the settings)."))
        self.b_check.clicked.connect(self.check_now)
        self.b_find = QPushButton(icon("system-search", "edit-find"), _("Which FM-1 does M-Flasher see?"))
        self.b_find.setObjectName("toolsFind")
        self.b_find.setToolTip(_("Runs “m-flasher find”: lists an FM-1 in ROM download mode and a running FM-1 on "
                                 "USB MIDI. Reads only – nothing is sent to the FM-1."))
        self.b_find.clicked.connect(self.find)
        self.b_backup = QPushButton(icon("document-save", "drive-harddisk"), _("Back up the whole flash"))
        self.b_backup.setObjectName("toolsFlashBackup")
        self.b_backup.setToolTip(_("Puts the FM-1 into USB download mode (by itself while it runs on USB MIDI, "
                                   "otherwise it tells you how), reads the whole flash twice with M-Flasher, compares "
                                   "both reads and keeps one copy in this device's backup folder. Reads only."))
        self.b_backup.clicked.connect(self.flash_backup)
        self.b_restore = QPushButton(icon("document-revert", "edit-undo"), _("Write flash back …"))
        self.b_restore.setObjectName("toolsFlashRestore")
        self.b_restore.setToolTip(_("Writes a whole-flash backup back onto the FM-1 it came from (same unit ID and "
                                    "flash chip), after a fresh backup of its current state and your confirmation."))
        self.b_restore.clicked.connect(self.flash_restore)
        lay.addWidget(flow_row(self.b_check, self.b_find, self.b_backup, self.b_restore))
        self.b_dl = QPushButton(icon("go-down", "system-run"), _("Into download mode"))
        self.b_dl.setObjectName("toolsDownloadMode")
        self.b_dl.setToolTip(_("Puts the FM-1 into USB download mode – by itself only when the app recognised the FM-1 "
                               "(original firmware, Felucca family, SLOOP 3) at its USB port, otherwise it tells you "
                               "how. Writes nothing."))
        self.b_dl.clicked.connect(self.download_mode)
        self.b_leave = QPushButton(icon("view-refresh", "system-reboot"), _("Restart the FM-1"))
        self.b_leave.setObjectName("toolsRestart")
        self.b_leave.setToolTip(_("Leaves download mode: M-Flasher starts the FM-1's firmware again (writes nothing)."))
        self.b_leave.clicked.connect(self.restart_unit)
        self.b_mboot = QPushButton(icon("system-software-install", "document-import"), _("Install m-bootloader"))
        self.b_mboot.setObjectName("toolsInstallMboot")
        self.b_mboot.setToolTip(_("Writes the bundled m-bootloader into the boot area (0x00000–0x03FFF) with "
                                  "M-Flasher – after a whole-flash backup and your confirmation."))
        self.b_mboot.clicked.connect(self.install_mboot)
        self.b_unboot = QPushButton(icon("edit-undo", "document-revert"), _("Remove m-bootloader (original boot area)"))
        self.b_unboot.setObjectName("toolsRemoveMboot")
        self.b_unboot.setToolTip(_("Puts back the boot area of M-VAVE's original V15 package (0x00000–0x03FFF) with "
                                   "M-Flasher – removes m-bootloader and what FM1 Open changed there; after a "
                                   "whole-flash backup and your confirmation."))
        self.b_unboot.clicked.connect(self.remove_mboot)
        self.b_import = QPushButton(icon("document-import", "document-open"), _("Import a flash backup …"))
        self.b_import.setObjectName("toolsFlashImport")
        from .flash_tools import import_tip
        self.b_import.setToolTip(import_tip())
        self.b_import.clicked.connect(self.flash_import)
        self.b_sloop3 = QPushButton(icon("system-software-install", "document-import"), _("Install SLOOP 3 (MIDI MODE) …"))
        self.b_sloop3.setObjectName("toolsInstallSloop3")
        self.b_sloop3.setToolTip(_("Writes the bundled SLOOP 3 through m-bootloader's MIDI MODE – after a whole-flash "
                                   "backup read twice and your confirmation; without m-bootloader its first step is "
                                   "installing it."))
        self.b_sloop3.clicked.connect(self.install_sloop3)
        self.b_s3check = QPushButton(icon("view-refresh", "document-preview"), _("Check SLOOP 3 in the flash …"))
        self.b_s3check.setObjectName("toolsCheckSloop3")
        self.b_s3check.setToolTip(_("Compares the flash with what the app's last SLOOP 3 install on this FM-1 wrote: "
                                    "one read in download mode once SLOOP 3 runs – nothing is written."))
        self.b_s3check.clicked.connect(self.check_sloop3)
        self.b_back = QPushButton(icon("edit-undo", "go-previous"), _("Back to …"))
        self.b_back.setObjectName("toolsWayBack")
        self.b_back.setToolTip(_("The way back with one button: to the last whole-flash backup, to SLOOP 3, to a "
                                 "Felucca-family firmware or to the original V15 – each with its route and its own "
                                 "questions."))
        self.b_back.clicked.connect(self.way_back)
        lay.addWidget(flow_row(self.b_dl, self.b_leave, self.b_mboot, self.b_unboot, self.b_import))
        lay.addWidget(flow_row(self.b_sloop3, self.b_s3check, self.b_back))
        self.boot_label = QLabel("")
        self.boot_label.setObjectName("toolsBootArea")
        self.boot_label.setWordWrap(True)
        self.boot_label.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.boot_label)
        self.rows: dict[str, dict] = {}
        self.refresh()

    @property
    def store(self):
        if self._store is None:
            from ..tools.store import Store
            self._store = Store()
        return self._store

    # ------------------------------------------------------------ view
    def refresh(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        heads = (_("Tool"), _("Bundled"), _("In use"), _("Checksum"), _("Last check"))
        for col, h in enumerate(heads):
            lab = QLabel(f"<b>{h}</b>")
            self.grid.addWidget(lab, 0, col)
        try:
            rows = updates.status(self.store)
        except Exception as e:  # noqa: BLE001 – the page must always open
            self.note.setText(_("The tool status could not be read: {error}").format(error=e))
            return
        notes = []
        self.rows = {}
        for r, s in enumerate(rows, start=1):
            name = QLabel(f"{s.title} <small>({s.license})</small>")
            bundled = QLabel(s.bundled if s.bundled_present or s.key != "m-flasher" else
                             _("{version} (not in this installation)").format(version=s.bundled))
            use = QLabel((s.active + ("" if s.active_source == "bundled" else " " + _("(update)"))) if s.active
                         else "–")
            ok = QLabel(_("verified") if s.verified else _("not verified"))
            ok.setObjectName(f"toolsVerified-{s.key}")
            last = QLabel(_when(s.last_check))
            lic = QPushButton(_("Show licence"))
            lic.setObjectName(f"toolsLicence-{s.key}")
            lic.clicked.connect(lambda _c=False, k=s.key, t=s.title: self.show_licence(k, t))
            for col, w in enumerate((name, bundled, use, ok, last, lic)):
                self.grid.addWidget(w, r, col)
            row = {"status": s, "verified": ok, "licence": lic}
            if s.offer is not None:
                up = QPushButton(icon("download"), _("Update"))
                up.setObjectName(f"toolsUpdate-{s.key}")
                up.setToolTip(offer_text(s.offer))
                up.clicked.connect(lambda _c=False, o=s.offer: self.update_tool(o))
                self.grid.addWidget(up, r, 6)
                row["update"] = up
                notes.append(offer_text(s.offer))
            if s.unavailable:
                notes.append(s.unavailable)
            for v in s.unknown_cli:
                notes.append(_("{tool} {version} was downloaded, but its commands differ from the ones this app "
                               "knows, so the app does not use it and keeps using {active}. An update of this app "
                               "can take it into use.").format(tool=s.title, version=v, active=s.active or "–"))
            if s.error:
                notes.append(_("{tool}: the last check did not get an answer ({error}).").format(tool=s.title,
                                                                                               error=s.error))
            self.rows[s.key] = row
        usable = not rows[0].unavailable if rows else False
        for b in (self.b_find, self.b_backup, self.b_restore, self.b_dl, self.b_leave, self.b_mboot, self.b_unboot):
            b.setEnabled(usable)
        self.note.setText("\n".join(notes))
        self.boot_label.setText(self.boot_text())
        try:                                       # (a write that did not end verified: the unit stays in download
            from ..tools import flashjob as FJ    #  mode – no Restart button until it is settled)
            if FJ.unfinished_write() is not None:
                self.b_leave.setEnabled(False)
                self.b_leave.setToolTip(_("A write did not end verified: the FM-1 stays in download mode until it "
                                          "is settled (the question at the next start, or “Write flash back …”)."))
        except Exception:  # noqa: BLE001
            pass

    def boot_text(self) -> str:
        """what the app knows of the active FM-1's boot area (tools/bootarea.py)"""
        try:
            from ..tools import bootarea
            prof = None if getattr(self.ctx, "is_virtual", False) else getattr(self.ctx, "profile", None)
            if prof is None:
                return ""
            return _("Boot area of “{device}”: {state}.").format(device=prof.name,
                                                                  state=bootarea.describe(bootarea.current(prof)))
        except Exception:  # noqa: BLE001 – only a hint
            return ""

    def show_licence(self, key: str, title: str):
        LicenceDialog(title, updates.licence_text(key, self.store), self).exec()

    # ------------------------------------------------------------ actions
    def check_now(self):
        if self._busy:
            return
        self._busy = True
        self.b_check.setEnabled(False)
        settings = getattr(self.ctx, "settings", None)

        def done(_res):
            self._busy = False
            self.b_check.setEnabled(True)
            self.refresh()

        def fail(msg, _tb=""):
            self._busy = False
            self.b_check.setEnabled(True)
            self.refresh()
            self.note.setText(_("Not checked: {error}").format(error=msg))
        run_job(self, lambda w: updates.check(settings, self.store), on_done=done, on_fail=fail)

    def update_tool(self, offer):
        if self._updating:                       # (a second click while the download runs)
            return
        notes = (offer.notes or "").strip()
        if len(notes) > 1500:
            notes = notes[:1500] + " …"
        text = (offer_text(offer) + "\n\n" + (notes + "\n\n" if notes else "") +
                _("The app downloads it, checks it against the checksum GitHub publishes for the file and keeps the "
                  "version you have now as the fallback until the new one has worked once. It does not install "
                  "anything on the FM-1."))
        if offer.key == "m-flasher":
            text += "\n\n" + _("To check it, the app starts the new M-Flasher once on this computer (it asks only for "
                               "its version and its list of commands). Afterwards it runs on this computer for "
                               "reading (device info); writing stays with the version this app was reviewed with. "
                               "The new version is trusted as its author published it on GitHub. If its commands "
                               "differ from the ones this app knows, it is kept but not used.")
        if not confirm(self, _("Update {tool}?").format(tool=dict(updates.titles()).get(offer.key, offer.key)), text,
                       _("Update")):
            return
        self._updating = True
        for row in self.rows.values():
            if row.get("update") is not None:
                row["update"].setEnabled(False)

        def done(c):
            self._updating = False
            self.refresh()
            self.note.setText(_("{tool} {version} is ready. Until it has worked once, the previous version stays "
                                "as the fallback.").format(tool=dict(updates.titles()).get(c.key, c.key),
                                                           version=c.version))

        def fail(msg, _tb=""):
            self._updating = False
            self.refresh()
            show_error(self, _("Not updated"), msg)
        run_job(self, lambda w: updates.download_update(offer, self.store), on_done=done, on_fail=fail)

    def flash_backup(self):
        from .flash_tools import backup_flow
        p = backup_flow(self, self.ctx, store=self._store)
        if p is not None:
            self.note.setText(_("Flash backup kept: {path}").format(path=p))

    def flash_restore(self):
        from .flash_tools import restore_flow
        restore_flow(self, self.ctx, store=self._store)

    def flash_import(self):
        from .flash_tools import import_flow
        res = import_flow(self, self.ctx)
        if res is not None:
            self.note.setText(_("Flash backup kept: {path}").format(path=res.path))

    def download_mode(self):
        from .flash_tools import download_flow
        d = download_flow(self, self.ctx, store=self._store)
        if d:
            self.note.setText(_("In ROM download mode: {device}").format(device=d))

    def restart_unit(self):
        from .flash_tools import leave_flow
        leave_flow(self, self.ctx, store=self._store)

    def install_mboot(self):
        from .flash_tools import install_boot_flow
        install_boot_flow(self, self.ctx, store=self._store)
        self.refresh()

    def install_sloop3(self):
        from .sloop3_ui import install_flow
        install_flow(self, self.ctx, store=self._store)
        self.refresh()

    def check_sloop3(self):
        from .sloop3_ui import verify_flow
        verify_flow(self, self.ctx, None, store=self._store)

    def way_back(self):
        from .flash_tools import way_back_flow
        way_back_flow(self, self.ctx, store=self._store)
        self.refresh()

    def remove_mboot(self):
        from .flash_tools import remove_boot_flow
        remove_boot_flow(self, self.ctx, store=self._store)
        self.refresh()

    def find(self):
        from ..tools import runner

        def work(w):
            return runner.find(store=self.store)

        def done(res):
            r, found = res
            if found is None:
                from ..tools.mflasher_parse import refusal
                self.note.setText(_("M-Flasher did not answer as expected: {reason}").format(
                    reason=refusal(r.err) or (_("no answer within the time limit") if r.timed_out else r.rc)))
                return
            lines = [_("In ROM download mode: {device}").format(device=u.device) for u in found.download]
            lines += [_("Running on USB MIDI: {name}").format(name=n) for n in found.midi]
            if found.sg_missing:
                lines.append(_("An FM-1 is in download mode, but the Linux driver “sg” is not loaded. The app's "
                               "installer sets it up; by hand: sudo modprobe sg"))
            self.note.setText("\n".join(lines) or _("M-Flasher sees no FM-1."))

        def fail(msg, _tb=""):
            self.note.setText(msg)
        run_job(self, work, on_done=done, on_fail=fail)
