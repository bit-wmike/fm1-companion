# SPDX-License-Identifier: GPL-3.0-only
"""Reiter „Sample-Pakete“: kostenlose Sammlungen von GitHub durchsehen und laden."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
                             QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import samplepacks as P
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
        self.b_index = QPushButton(icon("view-refresh"), "Inhalt anzeigen")
        self.b_index.setToolTip("Lädt nur die Ordnerstruktur (wenige hundert KiB)")
        self.b_index.clicked.connect(lambda: self.load_index())
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Ordner filtern: kick, snare, bongo …")
        self.filter.textChanged.connect(self._fill)
        row.addWidget(self.b_index)
        row.addWidget(self.filter, 1)
        rl.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Ordner", "Dateien"])
        self.tree.setRootIsDecorated(False)
        rl.addWidget(self.tree, 1)
        row2 = QHBoxLayout()
        self.b_sel = QPushButton(icon("download", "document-save"), "Angehakte Ordner laden")
        self.b_sel.setProperty("variant", "primary")
        self.b_sel.clicked.connect(lambda: self.download(selected=True))
        self.b_all = QPushButton(icon("download"), "Ganzes Paket laden")
        self.b_all.clicked.connect(lambda: self.download(selected=False))
        self.b_cancel = QPushButton("Abbrechen")
        self.b_cancel.setToolTip("Herunterladen anhalten – schon geladene Dateien bleiben")
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
        self.status = QLabel("Wähle links ein Paket. Geladene Sounds landen in ~/FM-1/Samples/Pakete und erscheinen "
                             "in der Bibliothek – mit Quelle und Lizenz in den Notizen.")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "muted")
        rl.addWidget(self.status)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        self.packs.setCurrentRow(0)

    def _do_cancel(self):
        self._cancel = True
        self.b_cancel.setEnabled(False)
        self.status.setText("Breche ab …")

    def current(self) -> P.Pack:
        return P.pack(self.packs.currentItem().data(Qt.ItemDataRole.UserRole))

    def _show(self, _row):
        p = self.current()
        self.title.setText(p.name)
        self.desc.setText(p.description + (f"  ({p.size_hint})" if p.size_hint else ""))
        if p.free:
            self.lic.setText(f"<b style='color:#27ae60'>✔ {P.licence_text(p)}</b><br>{p.licence_note} "
                             f"Quelle: <a href='{p.url}'>{p.url}</a>")
        else:
            self.lic.setText(f"<b style='color:#e67e22'>⚠ Lizenz unklar</b> – {p.licence_note}<br>"
                             f"Quelle: <a href='{p.url}'>{p.url}</a>")
        self._fill()

    def _fill(self, *_):
        self.tree.clear()
        p = self.current()
        ents = self.entries.get(p.id)
        if ents is None:
            self.tree.addTopLevelItem(QTreeWidgetItem(["Noch nicht geladen – „Inhalt anzeigen“ drücken.", ""]))
            return
        words = self.filter.text().lower().split()
        for folder, (n, _b) in P.folders(ents).items():
            if words and not all(w in folder.lower() for w in words):
                continue
            it = QTreeWidgetItem([folder or "(oberste Ebene)", str(n)])
            it.setData(0, Qt.ItemDataRole.UserRole, folder)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(it)
        self.tree.resizeColumnToContents(0)

    def load_index(self, refresh: bool = False):
        p = self.current()
        self.status.setText("Lese den Inhalt … (nur die Ordnerstruktur)")
        self.b_index.setEnabled(False)

        def done(ents):
            self.entries[p.id] = ents
            self.b_index.setEnabled(True)
            self.status.setText(f"{len(ents)} Audiodateien in {len(P.folders(ents))} Ordnern. Ordner anhaken und laden.")
            self._fill()

        def fail(m, tb):
            self.b_index.setEnabled(True)
            self.status.setText(f"Inhalt nicht lesbar: {m}")
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
            show_error(self, "Sample-Pakete", "Erst „Inhalt anzeigen“ – dann siehst du Ordner, Größe und Lizenz und "
                       "kannst einzelne Ordner anhaken oder das ganze Paket laden. Noch wurde nichts geladen.")
            return
        folders = self.checked() if selected else None
        if selected and not folders:
            show_error(self, "Sample-Pakete", "Hake mindestens einen Ordner an.")
            return
        ents = self.entries[p.id]
        n_files = len([e for e in ents if not folders or e.folder in folders])
        size = sum(e.size for e in ents if not folders or e.folder in folders)
        what = (f"{n_files} Dateien" + (f" ({size / 1e6:.0f} MB)" if size else "") + " aus „" + p.name + "“ laden"
                + (f" ({len(folders)} Ordner)" if folders else " (ganzes Paket)") + " – nach ~/FM-1/Samples/Pakete.")
        if not p.free:
            if not confirm(self, "Lizenz unklar", f"{what}\n\n⚠ {P.licence_text(p)}.\n{p.licence_note}\n\n"
                           "Nur zum Ausprobieren herunterladen (nicht in Veröffentlichungen verwenden)?", "Herunterladen"):
                return
        elif not confirm(self, "Sample-Paket laden", f"{what}\n\nLizenz: {P.licence_text(p)}.", "Herunterladen"):
            return
        dest = self.ctx.settings.samples_dir / "Pakete"
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
            if err == "abgebrochen":
                self.status.setText("Abgebrochen – schon geladene Dateien bleiben in Samples › Pakete; ein neuer "
                                    "Start lädt nur den Rest.")
                self.downloaded.emit([])
                return
            if err:
                self.status.setText(f"Laden fehlgeschlagen: {err} Schon geladene Dateien bleiben.")
                notify(f"Sample-Paket nicht geladen: {err}", "error")
                return
            notify(f"Sample-Paket „{P.folder_name(p)}“: {len(files)} Dateien geladen.", "success")
            self.status.setText(f"{len(files)} Dateien geladen nach Samples › Pakete › {P.folder_name(p)}"
                                + ("" if p.free else " – Lizenz unklar: nur zum Ausprobieren."))
            self.downloaded.emit([str(f) for f in files])
        self.dl_job = run_job(self, work, on_done=lambda r: finish(r), on_log=self.status.setText,
                              on_fail=lambda m, tb: finish(err=m))
