# SPDX-License-Identifier: GPL-3.0-only
"""Projekte: die 32 Plätze auf dem Gerät und die Projekt-Bibliothek auf dem Rechner."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QFileDialog, QGroupBox, QInputDialog, QLabel, QLineEdit, QProgressBar,
                             QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import backup, projects
from .common import (FlowLayout, confirm, device_tag, flow_row, fmt_date, fmt_size, fs_error_text, heading, icon,
                     same_device, show_error)
from .playback_bar import DeviceBadge, device_name
from .responsive import adaptive_splitter
import time


class ProjectsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.slots: list[projects.SlotInfo] = []
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Projekte", "Projekte vom aktiven FM-1 auf den Rechner holen, ansehen und anhören, "
                                          "benennen, sammeln und in jeden Platz zurückspielen – auch auf ein anderes "
                                          "FM-1 mit derselben Firmware."))
        self.badge = DeviceBadge(ctx, "Links siehst du die Plätze von")
        lay.addWidget(self.badge)
        split = QSplitter()
        lay.addWidget(split, 1)

        # Gerät
        g = QGroupBox("Auf dem FM-1")
        self.dev_box = g
        gl = QVBoxLayout(g)
        self.dev_tree = QTreeWidget()
        self.dev_tree.setHeaderLabels(["Platz", "Belegt", "Größe"])
        self.dev_tree.setRootIsDecorated(False)
        self.dev_tree.itemDoubleClicked.connect(lambda *_: self._view_slot())
        self.dev_tree.setToolTip("Doppelklick: ansehen und anhören")
        gl.addWidget(self.dev_tree, 1)
        self.dev_info = QLabel("Noch nicht gelesen.")
        self.dev_info.setWordWrap(True)
        gl.addWidget(self.dev_info)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        self.progress.hide()
        gl.addWidget(self.progress)
        b_read = QPushButton(icon("view-refresh"), "Neu lesen")
        b_read.setToolTip("Die Plätze werden beim Öffnen der Seite automatisch gelesen – hier bei Bedarf erneut (F5)")
        b_read.clicked.connect(self.read_slots)
        QShortcut(QKeySequence("F5"), self, activated=self.read_slots)
        self.b_view_slot = QPushButton(icon("media-playback-start", "document-preview"), "Ansehen && anhören")
        self.b_view_slot.setToolTip("Den gewählten Platz vom FM-1 lesen (nur lesen) und in „Ansicht & Export“ öffnen – "
                                    "dort auf dem FM-1 mit seinen Klängen abspielen oder als MIDI exportieren")
        self.b_view_slot.clicked.connect(self._view_slot)
        b_exp = QPushButton(icon("go-next", "document-export"), "Auf den Rechner holen")
        b_exp.setToolTip("Den gewählten Platz als Projektdatei in die Bibliothek kopieren (am FM-1 ändert sich nichts)")
        b_exp.clicked.connect(self._export)
        b_all = QPushButton(icon("document-save-all"), "Alle holen")
        b_all.setToolTip("Alle belegten Plätze in die Bibliothek kopieren")
        b_all.clicked.connect(self._export_all)
        b_erase = QPushButton(icon("edit-clear", "edit-delete"), "Platz leeren …")
        b_erase.setToolTip("Löscht den Platz auf dem FM-1 – vorher legt die App eine Kopie in der Bibliothek ab")
        b_erase.clicked.connect(self._erase)
        # gruppiert: ansehen | sichern (nur lesen) | ändert das FM-1 – Kurztitel vorn in der Reihe (keine Extrazeilen)
        gl.addWidget(flow_row(_section("Ansehen:"), self.b_view_slot, b_read))
        gl.addWidget(flow_row(_section("Sichern:"), b_exp, b_all))
        gl.addWidget(flow_row(_section("Ändert das FM-1:"), b_erase))
        split.addWidget(g)

        # Bibliothek
        lg = QGroupBox("Bibliothek auf dem Rechner")
        ll = QVBoxLayout(lg)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Name, Notiz, Firmware) …")
        self.search.textChanged.connect(self.refresh_library)
        ll.addWidget(self.search)
        self.lib = QTreeWidget()
        self.lib.setHeaderLabels(["Name", "Firmware", "Gerät", "Datum", "Größe", "Notiz"])
        self.lib.setRootIsDecorated(False)
        self.lib.setSortingEnabled(True)
        ll.addWidget(self.lib, 1)
        self.lib_empty = QLabel("Noch keine Projekte in der Bibliothek – so geht's: links einen belegten Platz wählen und "
                                "„Auf den Rechner holen“, oder „Alle holen“. Projektdateien (.fm1proj) lassen sich auch "
                                "mit „Datei hinzufügen“ aufnehmen.")
        self.lib_empty.setWordWrap(True)
        self.lib_empty.setProperty("role", "muted")
        ll.addWidget(self.lib_empty)
        self.lib.itemDoubleClicked.connect(lambda *_: self._view_lib())
        self.lib.setToolTip("Doppelklick: ansehen und anhören")
        b_view = QPushButton(icon("media-playback-start", "document-preview"), "Ansehen && anhören")
        b_view.setToolTip("Das gewählte Projekt in „Ansicht & Export“ öffnen – Spuren, Patterns, Song; abspielen auf "
                          "dem FM-1 oder im Rechner; MIDI-Export")
        b_view.clicked.connect(self._view_lib)
        self.b_imp = QPushButton(icon("go-previous", "document-import"), "Aufs FM-1 in gewählten Platz …")
        self.b_imp.setToolTip("Das gewählte Projekt in den links gewählten Platz des aktiven FM-1 schreiben (mit "
                              "Bestätigung; das Gerät prüft vorher)")
        self.b_imp.clicked.connect(self._import)
        b_ren = QPushButton(icon("edit-rename"), "Umbenennen")
        b_ren.clicked.connect(self._rename)
        b_note = QPushButton(icon("document-edit"), "Notiz")
        b_note.clicked.connect(self._note)
        b_del = QPushButton(icon("edit-delete"), "Löschen")
        b_del.clicked.connect(self._delete)
        b_add = QPushButton(icon("document-open"), "Datei hinzufügen …")
        b_add.clicked.connect(self._add_file)
        b_bk = QPushButton(icon("document-preview", "backup"), "Aus Backups …")
        b_bk.setToolTip("Projekte in älteren Backups ansehen und einzeln holen (Backups › Durchsuchen & Vergleichen)")
        b_bk.clicked.connect(self._from_backups)
        b_open = QPushButton(icon("folder-open"), "Ordner")
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ctx.settings.projects_dir))))
        ll.addWidget(flow_row(_section("Ansehen:"), b_view, self.b_imp))
        ll.addWidget(flow_row(_section("Ordnen:"), b_ren, b_note, b_del))
        ll.addWidget(flow_row(_section("Hinzufügen:"), b_add, b_bk, b_open))
        split.addWidget(lg)
        split.setChildrenCollapsible(False)
        g.setMinimumWidth(280)
        lg.setMinimumWidth(320)
        split.setStretchFactor(1, 2)
        adaptive_splitter(split)                 # schmal: Plätze über der Bibliothek
        self.active = False
        self.reading = False
        self._read_for = None                        # device_tag, für das die Plätze gelesen wurden
        ctx.changed.connect(self._dev_changed)
        self._titles()
        self.refresh_library()

    def _titles(self):
        name = device_name(self.ctx)
        self.dev_box.setTitle(f"Auf dem FM-1 „{name}“" if name else "Auf dem FM-1 (keins verbunden)")
        self.b_imp.setText(f"Auf „{name}“ in gewählten Platz …" if name else "Aufs FM-1 in gewählten Platz …")

    def _dev_changed(self):
        self._titles()
        if self._read_for is not None and same_device(self._read_for, device_tag(self.ctx)) and self.slots:
            return                                   # gleiches Gerät, nur neue Infos
        self.dev_tree.clear()
        self.slots = []
        self._read_for = None
        self.dev_info.setText("Noch nicht gelesen." if self.ctx.connected else "Kein Gerät verbunden.")
        if self.active and self.ctx.connected:
            self.read_slots(auto=True)

    def activate(self):
        """Seite (Reiter) geöffnet: Plätze automatisch lesen – wie bei den Presets."""
        self.active = True
        if self.ctx.connected and not self.slots and not self.reading:
            self.read_slots(auto=True)

    def deactivate(self):
        self.active = False

    # ------------------------------------------------------------ Gerät
    def read_slots(self, auto: bool = False):
        if auto:
            if not self.ctx.connected or self.reading:
                return
        elif not self.ctx.require(self):
            return
        if self.reading:
            return

        def work(w, link):
            w.progress.emit(0, 0)
            return projects.list_slots(link)

        def fail(msg, tb):
            self.reading = False
            self.progress.hide()
            self.dev_info.setText(f"Plätze lesen fehlgeschlagen: {msg}")
        self.reading = True
        self.progress.show()
        self.dev_info.setText("Lese die Projektplätze …")
        tag = device_tag(self.ctx)
        self.ctx.job(self, "Plätze lesen", work, dialog=False,
                     on_done=lambda res: self._got_slots(res, tag), on_fail=fail)

    def _got_slots(self, res, tag=None):
        self.reading = False
        self.progress.hide()
        self._read_for = tag or device_tag(self.ctx)
        self.slots, live = res
        keep = self._slot()                          # Auswahl über das Neu-Lesen hinweg behalten
        self.dev_tree.clear()
        sloop = getattr(self.ctx, "firmware_key", "") == "sloop"
        self.dev_tree.setHeaderLabels(["Section" if sloop else "Platz", "Belegt", "Größe"])
        for s in self.slots:
            it = QTreeWidgetItem([f"{s.slot:2d}" if not sloop else f"{self._slot_name(s.slot)} (Platz {s.slot})",
                                  "●" if s.used else "", fmt_size(s.size) if s.used else "leer"])
            it.setData(0, Qt.ItemDataRole.UserRole, s.slot)
            self.dev_tree.addTopLevelItem(it)
            if s.slot == keep:
                self.dev_tree.setCurrentItem(it)
        used = sum(1 for s in self.slots if s.used)
        if not self.slots:
            self.dev_info.setText("Diese Firmware meldet keine einzeln lesbaren Projektplätze. Backups gehen "
                                  "trotzdem (Seite „Backups“).")
        elif sloop:
            self.dev_info.setText(f"{used} von {len(self.slots)} Sections belegt, laufendes Projekt {fmt_size(live)}. "
                                  "SLOOP-Projekte lassen sich holen, sammeln und zurückspielen – ansehen geht nur im "
                                  "SLOOP-Web-Editor.")
        else:
            self.dev_info.setText(f"{used} von {len(self.slots)} Plätzen belegt, laufende Musik {fmt_size(live)}. "
                                  "Namen vergibst du in der Bibliothek (das Protokoll liefert keine).")
        if self.slots:
            self.dev_info.setText(self.dev_info.text() + f" Gelesen von „{device_name(self.ctx) or 'FM-1'}“ um "
                                  f"{time.strftime('%H:%M')}.")
        self.b_view_slot.setEnabled(not sloop)
        self.b_view_slot.setToolTip("SLOOP-Projekte zeigt der SLOOP-Web-Editor (Seite „Editor“)." if sloop else
                                    "Den gewählten Platz vom FM-1 lesen (nur lesen) und in „Ansicht & Export“ öffnen")

    def _view_slot(self):
        """Platz lesen und in der Ansicht zeigen (dort: abspielen auf dem FM-1, MIDI-Export)."""
        slot = self._slot()
        if slot is None:
            show_error(self, "Kein Platz gewählt", "Bitte links einen belegten Platz wählen.")
            return
        info = next((s for s in self.slots if s.slot == slot), None)
        if info is not None and not info.used:
            show_error(self, "Leer", f"{self._slot_word()} {self._slot_name(slot)} ist leer – es gibt nichts zu sehen.")
            return
        self._go_view(read_slot=slot)

    def _view_lib(self):
        it = self.lib.currentItem()
        if not it:
            show_error(self, "Kein Projekt gewählt", "Bitte rechts ein Projekt aus der Bibliothek wählen.")
            return
        self._go_view(path=it.data(0, Qt.ItemDataRole.UserRole))

    def _go_view(self, **kw):
        win = getattr(self.ctx, "window", None)
        if win is not None and hasattr(win, "go") and win.page("project_view") is not None:
            win.go("project_view", **kw)
            return
        from .pages.project_view import ProjectView           # ohne Hauptfenster (Tests): eigene Ansicht
        self._pv = ProjectView(self.ctx)
        self._pv.open_with(**kw)
        self._pv.show()

    def _slot(self) -> int | None:
        it = self.dev_tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _export(self):
        slot = self._slot()
        if slot is None or not self.ctx.require(self):
            return
        info = next((s for s in self.slots if s.slot == slot), None)
        word, nm = self._slot_word(), self._slot_name(slot)
        if info and not info.used:
            show_error(self, "Leer", f"{word} {nm} ist leer – es gibt nichts zu holen.")
            return
        default = f"{self.ctx.profile.name if self.ctx.profile else 'FM-1'} {word} {nm}"
        name, ok = QInputDialog.getText(self, "Projekt holen", f"Name für das Projekt aus {word} {nm}:", text=default)
        if not ok:
            return
        self._export_slots([(slot, name.strip() or default)])

    def _export_all(self):
        if not self.ctx.require(self):
            return
        used = [s for s in self.slots if s.used]
        if not used:
            show_error(self, "Nichts zu holen", "Auf dem FM-1 ist kein Projektplatz belegt – oder die Plätze wurden noch "
                                                "nicht gelesen („Neu lesen“, F5).")
            return
        prefix = self.ctx.profile.name if self.ctx.profile else "FM-1"
        self._export_slots([(s.slot, f"{prefix} {self._slot_word()} {self._slot_name(s.slot)}") for s in used])

    def _export_slots(self, items):
        fw = self.ctx.info.version if self.ctx.info else ""
        dev = self.ctx.profile.name if self.ctx.profile else ""
        folder = self.ctx.settings.projects_dir
        got: list = []

        def work(w, link):
            man = backup.list_objects(link)              # ein Inventar für alle (jedes LIST stoppt die Wiedergabe)
            for i, (slot, name) in enumerate(items):
                w.log.emit(f"{self._slot_word()} {self._slot_name(slot)} → {name}")
                got.append(projects.export_slot(link, slot, folder, name, fw, dev, manifest=man))
                w.progress.emit(i + 1, len(items))
            return list(got)
        self.ctx.job(self, "Projekte holen", work, cancellable=False,
                     done_text=lambda out: f"{len(out)} von {len(items)} Projekt(en) in die Bibliothek geholt "
                     f"({folder}). Am FM-1 wurde nichts geändert.",
                     fail_text=lambda m: f"Nicht alle Projekte geholt: {m} – {len(got)} von {len(items)} liegen in "
                     "der Bibliothek, am FM-1 wurde nichts geändert.")
        self.refresh_library()

    def _slot_word(self) -> str:
        return "Section" if getattr(self.ctx, "firmware_key", "") == "sloop" else "Platz"

    def _slot_name(self, slot: int) -> str:
        if getattr(self.ctx, "firmware_key", "") == "sloop" and 1 <= slot <= 4:
            return "ABCD"[slot - 1]
        return str(slot)

    def _erase(self):
        slot = self._slot()
        if slot is None or not self.ctx.require(self):
            return
        info = next((s for s in self.slots if s.slot == slot), None)
        word, nm = self._slot_word(), self._slot_name(slot)
        if info is not None and not info.used:
            show_error(self, "Schon leer", f"{word} {nm} ist schon leer.")
            return
        dev = self.ctx.profile.name if self.ctx.profile else "FM-1"
        size = f" ({fmt_size(info.size)})" if info is not None else ""
        if not confirm(self, f"{word} leeren",
                       f"{word} <b>{nm}</b> auf <b>{dev}</b> leeren{size}?<br><br>Vorher legt die App eine Kopie des "
                       "Projekts in der Bibliothek ab („… vor dem Leeren“) – so kannst du es jederzeit wieder "
                       "einspielen. Alles andere auf dem FM-1 bleibt unverändert.", "Leeren", danger=True):
            return
        fw = self.ctx.info.version if self.ctx.info else ""
        folder = self.ctx.settings.projects_dir
        saved: list = []

        def work(w, link):
            man = backup.list_objects(link)
            cur = next((o for o in man if o.id == backup.project_id(slot)), None)
            if cur is not None and cur.size:
                w.log.emit("Sichere das Projekt in die Bibliothek …")
                saved.append(projects.export_slot(link, slot, folder, f"{dev} {word} {nm} (vor dem Leeren)", fw, dev,
                                                  manifest=man, notes="Kopie, die die App vor dem Leeren angelegt hat."))
                w.log.emit(f"Gesichert: {saved[0].name}")
            w.log.emit(f"Leere {word} {nm} …")
            projects.erase_slot(link, slot)
            return projects.list_slots(link)
        d = self.ctx.job(self, f"{word} {nm} leeren", work,
                         done_text=lambda _r: f"{word} {nm} auf {dev} ist jetzt leer." +
                         (f" Das Projekt liegt als „{saved[0].stem}“ in der Bibliothek – von dort kannst du es "
                          "wieder einspielen." if saved else ""),
                         fail_text=lambda m: f"Nicht geleert: {m}" +
                         (f" (Die Kopie „{saved[0].stem}“ liegt trotzdem in der Bibliothek.)" if saved else ""),
                         cancel_tip="Das Leeren ist ein einzelner Schritt und lässt sich nicht abbrechen.")
        if d.ok:
            self._got_slots(d.result_value)
        self.refresh_library()

    # ------------------------------------------------------ Bibliothek
    def refresh_library(self):
        self.lib.clear()
        q = self.search.text().lower().split()
        try:
            lib = list(projects.library(self.ctx.settings.projects_dir))
        except OSError as e:
            self.dev_info.setText(fs_error_text(e, "Projekt-Bibliothek nicht lesbar"))
            lib = []
        for pf in lib:
            m = pf.meta
            hay = " ".join(str(m.get(k, "")) for k in ("name", "notes", "firmware", "device", "source")).lower()
            if any(w not in hay for w in q):
                continue
            it = QTreeWidgetItem([pf.name, m.get("firmware", ""), m.get("device", ""), fmt_date(m.get("created", "")),
                                  fmt_size(m.get("object", {}).get("size", 0)), m.get("notes", "")])
            it.setData(0, Qt.ItemDataRole.UserRole, str(pf.path))
            it.setToolTip(0, f"{pf.path}\nHerkunft: {m.get('source', '')}\nFormat: {m.get('kind', '')}")
            self.lib.addTopLevelItem(it)
        for c in range(5):
            self.lib.resizeColumnToContents(c)
        if hasattr(self, "lib_empty"):
            self.lib_empty.setVisible(self.lib.topLevelItemCount() == 0)

    def _pf(self) -> projects.ProjectFile | None:
        it = self.lib.currentItem()
        if not it:
            return None
        try:
            return projects.load_project(Path(it.data(0, Qt.ItemDataRole.UserRole)))
        except Exception as e:  # noqa: BLE001
            show_error(self, "Projektdatei beschädigt", str(e))
            return None

    def _import(self):
        pf = self._pf()
        slot = self._slot()
        if not pf or not self.ctx.require(self):
            return
        fw_dev = self.ctx.info.version if self.ctx.info else ""
        src_fam = projects.family(pf.meta.get("firmware", "")) or projects.family_of_data(pf.data)
        dst_fam = projects.family(fw_dev) or projects.family(getattr(self.ctx, "firmware_key", "") or "")
        name_of = {"melodee": "Melodee", "sloop": "SLOOP", "skiff": "Skiff"}
        if src_fam and dst_fam and src_fam != dst_fam:
            show_error(self, "Andere Firmware",
                       f"<b>{pf.name}</b> stammt von <b>{name_of.get(src_fam, src_fam)}</b>, auf dem FM-1 läuft "
                       f"<b>{name_of.get(dst_fam, dst_fam)}</b>. Die Projektformate passen nicht zusammen – es wird "
                       "nichts geschrieben.")
            return
        if not self.slots and self.ctx.connected:           # Belegung erst lesen, damit die Bestätigung stimmt
            def read(w, link):
                return projects.list_slots(link)
            d0 = self.ctx.job(self, "Plätze lesen", read, close_on_success=True)
            if not d0.ok:
                return
            self._got_slots(d0.result_value)
        n_slots = len(self.slots) or (4 if dst_fam == "sloop" else 32)
        word = self._slot_word()
        if slot is None:
            slot, ok = QInputDialog.getInt(self, f"{word} wählen", f"In welchen Projektplatz (1–{n_slots})?", 1, 1, n_slots)
            if not ok:
                return
        if slot > n_slots:
            show_error(self, "Kein solcher Platz", f"Diese Firmware hat nur {n_slots} Projektplätze.")
            return
        nm = self._slot_name(slot)
        occupied = next((s for s in self.slots if s.slot == slot and s.used), None)
        warn = ""
        if pf.meta.get("firmware") and fw_dev and pf.meta["firmware"].strip() != fw_dev.strip():
            warn = (f"<br><br>Das Projekt stammt von <b>{pf.meta['firmware']}</b>, auf dem FM-1 läuft <b>{fw_dev}</b> "
                    "(gleiche Firmware, andere Version – das FM-1 prüft, ob es das Format liest).")
        text = (f"<b>{pf.name}</b> in {word} <b>{nm}</b> auf <b>{self.ctx.profile.name if self.ctx.profile else 'dem FM-1'}</b> "
                "spielen?" +
                (f"<br><br><b>{word} {nm} ist belegt ({fmt_size(occupied.size)}) und wird überschrieben.</b> Hol es "
                 "vorher auf den Rechner, wenn du es behalten willst." if occupied else
                 f"<br><br>{word} {nm} ist leer.") + warn +
                "<br><br>Das Gerät prüft das Projekt vorher – passt es nicht, wird nichts geschrieben. "
                "Alles andere auf dem FM-1 bleibt unverändert.")
        if not confirm(self, "Projekt einspielen", text, "Einspielen", danger=bool(occupied)):
            return

        def work(w, link):
            projects.import_to_slot(link, pf, slot, device_firmware=fw_dev)
            w.log.emit(f"{word} {nm}: {pf.name} geschrieben. Am FM-1 unter SAVE > PROJECT laden.")
            return projects.list_slots(link)
        d = self.ctx.job(self, "Projekt einspielen", work,
                         done_text=f"„{pf.name}“ liegt jetzt in {word} {nm}. Am FM-1 unter SAVE > PROJECT laden.",
                         fail_text=lambda m: f"Nicht eingespielt: {m}" +
                         ("" if "nichts geschrieben" in str(m) else " – das Gerät hat nichts geschrieben."),
                         cancel_tip="Ein einzelnes Projekt wird in einem Zug geschrieben – bitte kurz warten.")
        if d.ok:
            self._got_slots(d.result_value)

    def _rename(self):
        pf = self._pf()
        if not pf:
            return
        name, ok = QInputDialog.getText(self, "Umbenennen", "Name:", text=pf.name)
        if ok and name.strip():
            self._update(pf, "Umbenannt", name=name.strip())

    def _note(self):
        pf = self._pf()
        if not pf:
            return
        txt, ok = QInputDialog.getMultiLineText(self, "Notiz", "Notiz:", pf.meta.get("notes", ""))
        if ok:
            self._update(pf, "Notiz gespeichert", notes=txt)

    def _update(self, pf, what: str, **changes):
        try:
            projects.update_meta(pf, **changes)
            self.ctx.message.emit(f"{what}: {pf.name}")
        except OSError as e:
            show_error(self, "Nicht gespeichert", fs_error_text(e, f"{pf.path.name} lässt sich nicht ändern"))
        self.refresh_library()

    def _delete(self):
        pf = self._pf()
        if pf and confirm(self, "Löschen", f"<b>{pf.name}</b> aus der Bibliothek löschen? Die Datei {pf.path.name} wird "
                                           "endgültig entfernt – am FM-1 ändert sich nichts.", "Löschen", danger=True):
            try:
                pf.path.unlink(missing_ok=True)
                self.ctx.message.emit(f"Aus der Bibliothek gelöscht: {pf.name}")
            except OSError as e:
                show_error(self, "Nicht gelöscht", fs_error_text(e, f"{pf.path.name} nicht gelöscht"))
            self.refresh_library()

    def _from_backups(self):
        win = getattr(self.ctx, "window", None)
        if win is not None and win.page("backup_browser") is not None:
            win.go("backup_browser")
        else:
            self._add_file()

    def _add_file(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Projekt- oder Backup-Dateien", str(Path.home()),
                                                "Projekte und Backups (*.fm1proj *.json)")
        n = 0
        for f in files:
            p = Path(f)
            try:
                if p.suffix == projects.SUFFIX:
                    pf = projects.load_project(p)
                    projects.save_project(self.ctx.settings.projects_dir, pf.name, pf.data, pf.meta.get("firmware", ""),
                                          pf.meta.get("device", ""), pf.meta.get("notes", ""), pf.meta.get("source", ""))
                    n += 1
                else:
                    a = backup.load_file(p)
                    for slot, data in projects.projects_from_backup(a):
                        projects.save_project(self.ctx.settings.projects_dir, f"{p.stem} Platz {slot}", data,
                                              a.firmware, source=f"{p.name}, Platz {slot}")
                        n += 1
            except OSError as e:
                show_error(self, "Nicht gespeichert", fs_error_text(e, f"{p.name} nicht in die Bibliothek übernommen"))
                break
            except Exception as e:  # noqa: BLE001
                show_error(self, "Datei übersprungen", f"{p.name}: {e}")
        if n:
            self.ctx.message.emit(f"{n} Projekt(e) in die Bibliothek übernommen.")
        self.refresh_library()


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "section")
    return lab


def _row(*ws):
    """Knopfreihe, die bei schmalem Fenster umbricht (None wird ignoriert)."""
    h = FlowLayout()
    for w in ws:
        if w is not None:
            h.addWidget(w)
    return h
