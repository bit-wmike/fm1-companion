# SPDX-License-Identifier: GPL-3.0-only
"""Reiter „Sample-Pakete“: kostenlose Sammlungen von GitHub durchsehen und laden."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
                             QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import samplepacks as P
from ..i18n import _, ngettext
from .common import confirm, icon, notify, run_job, show_error
from .responsive import reflow


class PacksTab(QWidget):
    downloaded = pyqtSignal(list)

    def __init__(self, ctx, lib, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.lib = lib
        self.entries: dict[str, list[P.Entry]] = {}
        self.busy = False
        self._cancel = False
        lay = QHBoxLayout(self)
        split = QSplitter()
        lay.addWidget(split)
        self.packs = QListWidget()
        self.packs.setMinimumWidth(230)
        for p in P.PACKS:
            it = QListWidgetItem(icon("folder-download", "folder-sound", "folder"), p.name)
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            it.setToolTip(P.licence_text(p))
            if not p.free:
                it.setText(p.name + "  ⚠")
            self.packs.addItem(it)
        self.packs.currentRowChanged.connect(self._show)
        split.addWidget(self.packs)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setProperty("role", "section")
        rl.addWidget(self.title)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        rl.addWidget(self.desc)
        self.lic = QLabel()
        self.lic.setWordWrap(True)
        self.lic.setTextFormat(Qt.TextFormat.RichText)
        self.lic.setOpenExternalLinks(True)
        rl.addWidget(self.lic)
        row = QHBoxLayout()
        self.b_index = QPushButton(icon("view-refresh"), _("Show content"))
        self.b_index.setToolTip(_("Loads only the folder structure (a few hundred KiB)"))
        self.b_index.clicked.connect(lambda: self.load_index())
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(_("Filter folders: kick, snare, bongo …"))
        self.filter.textChanged.connect(self._fill)
        row.addWidget(self.b_index)
        row.addWidget(self.filter, 1)
        rl.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Folder"), _("Files")])
        self.tree.setRootIsDecorated(False)
        rl.addWidget(self.tree, 1)
        row2 = QHBoxLayout()
        self.b_sel = QPushButton(icon("download", "document-save"), _("Load ticked folders"))
        self.b_sel.setProperty("variant", "primary")
        self.b_sel.clicked.connect(lambda: self.download(selected=True))
        self.b_all = QPushButton(icon("download"), _("Load whole pack"))
        self.b_all.clicked.connect(lambda: self.download(selected=False))
        self.b_cancel = QPushButton(_("Cancel"))
        self.b_cancel.setToolTip(_("Stop downloading – files already loaded stay"))
        self.b_cancel.clicked.connect(self._do_cancel)
        self.b_cancel.setEnabled(False)
        for b in (self.b_sel, self.b_all, self.b_cancel):
            row2.addWidget(b)
        row2.addStretch(1)
        rl.addLayout(row2)
        reflow(row2)                                     # Knöpfe brechen bei schmalem Fenster um
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        rl.addWidget(self.bar)
        self.status = QLabel(_("Choose a pack on the left. Downloaded sounds go to ~/FM-1/Samples/Pakete and appear "
                               "in the library – with source and licence in the notes."))
        self.status.setWordWrap(True)
        self.status.setProperty("role", "muted")
        rl.addWidget(self.status)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        self.packs.setCurrentRow(0)

    def _do_cancel(self):
        self._cancel = True
        self.b_cancel.setEnabled(False)
        self.status.setText(_("Cancelling …"))

    def current(self) -> P.Pack:
        return P.pack(self.packs.currentItem().data(Qt.ItemDataRole.UserRole))

    def _show(self, _row):
        p = self.current()
        self.title.setText(p.name)
        self.desc.setText(p.description + (f"  ({p.size_hint})" if p.size_hint else ""))
        if p.free:
            self.lic.setText(_("<b style='color:#27ae60'>✔ {licence}</b><br>{note} "
                               "Source: <a href='{url}'>{url}</a>").format(licence=P.licence_text(p), note=p.licence_note,
                                                                       url=p.url))
        else:
            self.lic.setText(_("<b style='color:#e67e22'>⚠ Licence unclear</b> – {note}<br>"
                               "Source: <a href='{url}'>{url}</a>").format(note=p.licence_note, url=p.url))
        self._fill()

    def _fill(self, *_args):
        self.tree.clear()
        p = self.current()
        ents = self.entries.get(p.id)
        if ents is None:
            self.tree.addTopLevelItem(QTreeWidgetItem([_("Not loaded yet – press “Show content”."), ""]))
            return
        words = self.filter.text().lower().split()
        for folder, (n, _b) in P.folders(ents).items():
            if words and not all(w in folder.lower() for w in words):
                continue
            it = QTreeWidgetItem([folder or _("(top level)"), str(n)])
            it.setData(0, Qt.ItemDataRole.UserRole, folder)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(it)
        self.tree.resizeColumnToContents(0)

    def load_index(self, refresh: bool = False):
        p = self.current()
        self.status.setText(_("Reading the content … (only the folder structure)"))
        self.b_index.setEnabled(False)

        def done(ents):
            self.entries[p.id] = ents
            self.b_index.setEnabled(True)
            self.status.setText(_("{n} audio files in {folders} folders. Tick folders and load them.").format(
                n=len(ents), folders=len(P.folders(ents))))
            self._fill()

        def fail(m, tb):
            self.b_index.setEnabled(True)
            self.status.setText(_("Content not readable: {error}").format(error=m))
        self.index_job = run_job(self, lambda w: P.index(p, refresh), on_done=done, on_fail=fail)

    def checked(self) -> list[str]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.CheckState.Checked:
                out.append(it.data(0, Qt.ItemDataRole.UserRole))
        return out

    def download(self, selected: bool = True):
        if self.busy:
            return
        p = self.current()
        if p.id not in self.entries:
            show_error(self, _("Sample packs"), _("First “Show content” – then you see folders, size and licence and "
                                                  "can tick single folders or load the whole pack. Nothing was loaded yet."))
            return
        folders = self.checked() if selected else None
        if selected and not folders:
            show_error(self, _("Sample packs"), _("Tick at least one folder."))
            return
        ents = self.entries[p.id]
        n_files = len([e for e in ents if not folders or e.folder in folders])
        size = sum(e.size for e in ents if not folders or e.folder in folders)
        files_text = (_("{n} files ({mb} MB)").format(n=n_files, mb=f"{size / 1e6:.0f}") if size
                      else _("{n} files").format(n=n_files))
        part = (ngettext("{n} folder", "{n} folders", len(folders)).format(n=len(folders)) if folders
                else _("whole pack"))
        what = _("Load {files} from “{pack}” ({part}) – to ~/FM-1/Samples/Pakete.").format(
            files=files_text, pack=p.name, part=part)
        if not p.free:
            if not confirm(self, _("Licence unclear"), _("{what}\n\n⚠ {licence}.\n{note}\n\n"
                                                         "Download for trying out only (do not use in publications)?").format(
                    what=what, licence=P.licence_text(p), note=p.licence_note), _("Download")):
                return
        elif not confirm(self, _("Load sample pack"), _("{what}\n\nLicence: {licence}.").format(what=what, licence=P.licence_text(p)),
                         _("Download")):
            return
        dest = self.ctx.settings.samples_dir / "Pakete"  # i18n: ignore (Ordnername)
        self.busy, self._cancel = True, False
        for b in (self.b_sel, self.b_all):
            b.setEnabled(False)
        self.b_cancel.setEnabled(True)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)

        def work(w):
            files = P.download(p, dest, folders, log=w.log.emit, cancel=lambda: self._cancel)
            for i, f in enumerate(files):
                self.lib.add_file(f, root=self.ctx.settings.samples_dir, tags=",".join(("paket", p.tag)),
                                  notes=P.note_for(p))
            return files

        def finish(files=None, err=None):
            self.busy = False
            for b in (self.b_sel, self.b_all):
                b.setEnabled(True)
            self.b_cancel.setEnabled(False)
            self.bar.setVisible(False)
            if err == _("cancelled"):
                self.status.setText(_("Cancelled – files already loaded stay in Samples › Pakete; a new "
                                      "start loads only the rest."))
                self.downloaded.emit([])
                return
            if err:
                self.status.setText(_("Loading failed: {error} Files already loaded stay.").format(error=err))
                notify(_("Sample pack not loaded: {error}").format(error=err), "error")
                return
            notify(_("Sample pack “{name}”: {n} files loaded.").format(name=P.folder_name(p), n=len(files)), "success")
            if p.free:
                self.status.setText(_("{n} files loaded to Samples › Pakete › {name}").format(n=len(files), name=P.folder_name(p)))
            else:
                self.status.setText(_("{n} files loaded to Samples › Pakete › {name} – licence unclear: for trying out only.").format(
                    n=len(files), name=P.folder_name(p)))
            self.downloaded.emit([str(f) for f in files])
        self.dl_job = run_job(self, work, on_done=lambda r: finish(r), on_log=self.status.setText,
                              on_fail=lambda m, tb: finish(err=m))
