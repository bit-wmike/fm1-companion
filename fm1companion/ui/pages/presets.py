# SPDX-License-Identifier: GPL-3.0-only
"""Presets: die 384 User-Plätze von Melodee (gemeinsame Bibliothek, Befehl 80) und die eigene Preset-Bibliothek."""
from __future__ import annotations

import json
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QMenu, QPushButton, QSpinBox, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import presetlib as pl
from ..common import (DeviceUndo, confirm, notify, device_tag, flow_row, fs_error_text, heading, icon, plural,
                      fw_label, safe_folder, same_device, show_error)
from . import PageSpec

HINT = ("Schreiben, Umbenennen und Löschen speichern im Flash des FM-1 und <b>stoppen dafür die Wiedergabe</b>. "
        "Favoriten, die du während der Wiedergabe änderst, speichert das FM-1 erst nach STOP.")


def _tree(headers):
    t = QTreeWidget()
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False)
    t.setSortingEnabled(True)
    t.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
    t.setAlternatingRowColors(True)
    return t


class PresetsPage(QWidget):
    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.caps: pl.Caps | None = None
        self.slots: list[dict] = []
        self.cache: dict[int, pl.Preset] = {}          # gelesene Plätze
        self.undo = DeviceUndo()                      # (Platz, altes Preset) je Gerät
        self._dev_tag = None
        self.fs_error = ""
        self.lib_items: list[pl.Preset] = []
        self.busy = False
        self.active = False
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Presets", "Die 384 User-Plätze deines FM-1 (Melodee ab 1.5) und deine eigene "
                                         "Preset-Bibliothek im Ordner FM-1/Presets – sichern, vergleichen, "
                                         "zurückspielen, anhören."))
        self.state = QLabel("")
        self.state.setWordWrap(True)
        self._note = ""                               # Ergebnis der letzten Aktion, bleibt nach dem Neu-Lesen stehen
        self._worker = None
        self.b_cancel = QPushButton(icon("process-stop", "dialog-cancel"), "Abbrechen")
        self.b_cancel.setToolTip("Laufenden Vorgang nach dem aktuellen Platz anhalten – schon Erledigtes bleibt")
        self.b_cancel.clicked.connect(self.cancel_job)
        self.b_cancel.hide()
        srow = QHBoxLayout()
        srow.addWidget(self.state, 1)
        srow.addWidget(self.b_cancel, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(srow)
        hint = QLabel(HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        lay.addWidget(hint)
        # Filter
        fl = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setMinimumWidth(160)
        self.search.setPlaceholderText("Suchen: Name, Engine, Platz, Schlagwort …  (Strg+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill)
        fl.addWidget(self.search, 1)
        self.engine = QComboBox()
        self.engine.addItem("alle Engines", None)
        self.engine.currentIndexChanged.connect(self.fill)
        self.engine.setToolTip("nach Engine filtern")
        fl.addWidget(self.engine)
        self.only_used = QCheckBox("nur belegte")
        self.only_used.setChecked(True)
        self.only_used.toggled.connect(self.fill)
        fl.addWidget(self.only_used)
        self.only_fav = QCheckBox("nur Favoriten ★")
        self.only_fav.toggled.connect(self.fill)
        fl.addWidget(self.only_fav)
        lay.addLayout(fl)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search.setFocus)

        sp = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(sp, 1)
        # ---- Gerät
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Am FM-1</b>"))
        self.dev = _tree(["Platz", "Engine", "Name", "★", "Art", "Bibliothek"])
        self.dev.itemSelectionChanged.connect(self._sel_changed)
        ll.addWidget(self.dev, 1)
        self.empty = QLabel("Noch kein FM-1 mit Melodee verbunden – so geht's: FM-1 per USB anstecken, "
                            "Melodee 1.5 oder neuer muss laufen.")
        self.empty.setWordWrap(True)
        ll.addWidget(self.empty)
        r1 = self._flow(ll)
        self.b_read = self._btn("Einlesen", "view-refresh", self.read_list, "Liste der Plätze neu lesen (F5)", r1)
        self.b_read.setProperty("variant", "primary")
        QShortcut(QKeySequence("F5"), self, activated=self.read_list)
        self.b_all = self._btn("Alle sichern", "document-save-all", self.save_all,
                               "alle belegten Plätze vollständig in die Bibliothek holen", r1)
        self.b_all.setProperty("variant", "primary")
        self.b_fetch = self._btn("In Bibliothek", "go-next", self.fetch_selected,
                                 "markierte Plätze in die Bibliothek holen", r1)
        # Anhören / Speichern auf einer Spur
        r2 = self._flow(ll)
        tl = QLabel("Spur:")
        tl.setToolTip("Spur am FM-1 zum Anhören / Speichern")
        r2.addWidget(tl)
        self.track = QSpinBox()
        self.track.setRange(1, 4)
        self.track.setToolTip("Spur am FM-1 zum Anhören / Speichern")
        r2.addWidget(self.track)
        self.b_load = self._btn("Anhören", "media-playback-start", self.load_track,
                                "Preset auf die Spur laden (ändert nur den Klang der Spur, speichert nichts)", r2)
        self.b_store = self._btn("Aktuellen Klang als Preset speichern …", "document-save", self.store_track,
                                 "den aktuellen Klang der Spur in diesen Platz speichern", r2)
        # seltener gebrauchte Aktionen: „Mehr“-Menü (und Kontextmenü der Liste)
        self.more = QMenu(self)
        self.b_ren = self._menu_action(self.more, "Umbenennen …", "edit-rename", self.rename, "Preset am FM-1 umbenennen (F2)")
        QShortcut(QKeySequence("F2"), self.dev, activated=self.rename)
        self.b_fav = self._menu_action(self.more, "Favorit ★ an/aus", "starred", self.toggle_fav, "Favorit an/aus")
        self.b_move = self._menu_action(self.more, "Verschieben/Tauschen …", "transform-move", self.move_slot,
                                        "Platz in einen anderen Platz verschieben oder zwei Plätze tauschen")
        self.more.addSeparator()
        self.b_del = self._menu_action(self.more, "Platz leeren …", "edit-delete", self.erase, "Platz am FM-1 leeren")
        self.b_undo = self._menu_action(self.more, "Rückgängig", "edit-undo", self.do_undo,
                                        "letzte Änderung am FM-1 zurücknehmen")
        self.b_more = QPushButton(icon("overflow-menu", "application-menu"), "Mehr ▾")
        self.b_more.setToolTip("Umbenennen, Favorit, Verschieben, Leeren, Rückgängig")
        self.b_more.setMenu(self.more)
        r2.addWidget(self.b_more)
        self.dev.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.dev.customContextMenuRequested.connect(lambda pos: self.more.exec(self.dev.viewport().mapToGlobal(pos)))
        sp.addWidget(left)
        # ---- Bibliothek
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("<b>Bibliothek</b> (alle Geräte)"))
        self.lib = _tree(["Name", "Engine", "Gerät", "Platz", "Schlagwörter", "Art"])
        self.lib.itemSelectionChanged.connect(self._sel_changed)
        rl.addWidget(self.lib, 1)
        self.lib_empty = QLabel("Noch keine Presets in der Bibliothek – so geht's: „Alle sichern“ holt alle "
                                "belegten Plätze vom FM-1, „Importieren“ liest .fm1preset-, Satz-, Editor- und .syx-Dateien.")
        self.lib_empty.setWordWrap(True)
        rl.addWidget(self.lib_empty)
        q1 = self._flow(rl)
        self.b_put = self._btn("In Platz schreiben …", "go-previous", self.write_selected,
                               "Bibliotheks-Preset in den markierten Platz am FM-1 schreiben", q1)
        self.b_cmp = self._btn("Vergleichen", "kompare", self.compare, "Platz am FM-1 mit Bibliotheks-Preset vergleichen", q1)
        self.b_tag = self._btn("Schlagwörter …", "tag", self.edit_tags, "Schlagwörter setzen (mit Komma getrennt)", q1)
        self.b_lren = self._btn("Umbenennen …", "edit-rename", self.lib_rename,
                                "Namen des Bibliotheks-Presets ändern (nur die Datei, am FM-1 nichts)", q1)
        q2 = self._flow(rl)
        self._btn("Importieren …", "document-import", self.import_files, "Dateien in die Bibliothek holen", q2)
        self.b_exp = self._btn("Exportieren …", "document-export", lambda: self.export(),   # (clicked liefert False als target)
                               "markierte Presets als Satz (.zip), einzeln (.fm1preset) oder für den Web-Editor (.json)", q2)
        self.b_ldel = self._btn("Entfernen", "edit-delete", self.lib_delete, "aus der Bibliothek löschen", q2)
        self._btn("Ordner öffnen", "folder-open", self.open_folder, "Bibliotheksordner im Dateimanager", q2)
        sp.addWidget(right)
        sp.setChildrenCollapsible(False)
        left.setMinimumWidth(300)
        right.setMinimumWidth(260)
        sp.setSizes([600, 500])
        ctx.changed.connect(self._dev_changed)
        self.refresh_library()
        self._dev_changed()

    # ------------------------------------------------------------ Grundlagen
    @staticmethod
    def _flow(parent_layout):
        w = flow_row()
        parent_layout.addWidget(w)
        return w.layout()

    def _btn(self, text, ic, fn, tip, lay):
        b = QPushButton(icon(ic), text)
        b.setToolTip(tip)
        b.clicked.connect(fn)
        lay.addWidget(b)
        return b

    @staticmethod
    def _menu_action(menu, text, ic, fn, tip):
        a = menu.addAction(icon(ic), text)
        a.setToolTip(tip)
        a.setStatusTip(tip)
        a.triggered.connect(fn)
        return a

    def backup_all(self):
        """Schnellzugriff der Übersicht: Plätze einlesen (falls nötig) und alle belegten sichern."""
        if not self.ctx.connected:
            self.ctx.require(self)
            return
        if self.slots and not self.busy:
            self.save_all()
            return
        self._save_after_read = True
        if not self.busy:
            self.read_list()

    @property
    def root(self) -> Path:
        p, self.fs_error = safe_folder("Presets")
        return p

    def library(self) -> pl.Library:
        return pl.Library(self.root)

    def device_name(self) -> str:
        return self.ctx.profile.folder_name if self.ctx.profile else "Unbekanntes Gerät"

    def activate(self):
        self.active = True
        if self.ctx.connected and self.caps is None and not self.busy:
            self.read_list()

    def deactivate(self):
        self.active = False

    def _dev_changed(self):
        tag = device_tag(self.ctx)
        if self._dev_tag is not None and same_device(tag, self._dev_tag) and self.slots:
            self._enable()                     # gleiches Gerät, nur neue Infos
            return
        self._dev_tag = tag
        self.caps, self.slots, self.cache = None, [], {}
        self._note = ""
        self.state.setText(self._status_text())
        self.fill()
        if self.ctx.connected and self.active:
            if self.busy:
                self._reread = True            # läuft noch ein Auftrag fürs alte Gerät: danach neu lesen
            else:
                self.read_list()

    def _after_job(self):
        if getattr(self, "_reread", False):
            self._reread = False
            if self.ctx.connected and self.active and not self.busy:
                self.read_list()

    def _status_text(self) -> str:
        """Zustandszeile ohne gelesene Liste (nach Gerätewechsel)."""
        c = self.ctx
        if c.connected:
            return f"{fw_label(c.title())}: Plätze noch nicht gelesen – „Einlesen“ (F5)."
        if getattr(c, "midi_connected", False):
            return (f"{fw_label(c.title())}: Diese Firmware hat keine Preset-Bibliothek über USB (nur MIDI). "
                    "Deine Bibliothek rechts kannst du trotzdem durchsuchen und exportieren.")
        return "Kein FM-1 verbunden – die Bibliothek rechts ist trotzdem nutzbar."

    def _job(self, title, fn, done, dialog=False, partial=None, cancellable=False, unit="Plätze"):
        """fn(worker, bank) exklusiv am Gerät. partial(): wird bei Fehlern aufgerufen, damit schon erledigte
        Schritte rückgängig-bar bleiben. cancellable: „Abbrechen“ anbieten (fn prüft worker.cancelled zwischen
        den Plätzen). Fortschritt (worker.progress) erscheint in der Zustandszeile."""
        if not self.ctx.require(self):
            return
        if self.busy:
            return
        self.busy = True
        self._enable()

        def work(w, link):
            if self.caps is None or not self.caps.shared:
                self.caps = pl.probe(link, self.ctx.info)
            if not self.caps.shared:
                raise pl.PresetError(pl.refusal_text(self.caps))
            return fn(w, pl.SharedBank(link, self.caps))

        tag = device_tag(self.ctx)
        self._job_tag = tag
        t0 = time.monotonic()

        def finish():
            self.busy = False
            self._worker = None
            self.b_cancel.hide()

        def ok(res):
            finish()
            if same_device(tag, device_tag(self.ctx)):
                done(res)
            elif partial:                      # Gerät gewechselt: nur das Rückgängig fürs alte Gerät behalten
                partial()
            self._enable()
            self._after_job()

        def fail(msg, tb):
            cancelled = self._worker is not None and self._worker.cancelled
            finish()
            if partial:
                partial()
            self._enable()
            QTimer.singleShot(0, self._after_job)
            if cancelled:
                self.state.setText(f"{title}: abgebrochen – am FM-1 wurde nichts weiter geändert."
                                   + (" Schon Geändertes nimmt „Rückgängig“ zurück." if partial and self.undo else ""))
                return
            if self.caps and not self.caps.shared:       # kein Fehler, sondern eine Eigenschaft der Firmware
                self.state.setText(f"{fw_label(self.ctx.title())}: keine User-Presets über USB – "
                                   "die Bibliothek rechts kannst du trotzdem nutzen. Am FM-1 wurde nichts geändert.")
                self.empty.setText(pl.refusal_text(self.caps))
                return
            self.state.setText(f"<b>{title} fehlgeschlagen:</b> {msg}"
                               + (" Bereits geänderte Plätze lassen sich mit „Rückgängig“ zurücknehmen."
                                  if partial and self.undo else ""))
            show_error(self, title, msg, tb)

        def progress(a, b):
            if b and self.busy:
                el = time.monotonic() - t0
                rest = f", noch etwa {int(el / a * (b - a)) + 1} s" if a and el > 1.5 and a < b else ""
                self.state.setText(f"{title} … {a} von {b} {unit}{rest}")
        self.state.setText(f"{title} …")
        self.b_cancel.setVisible(bool(cancellable))
        self._worker = self.ctx.job(self, title, work, dialog=False, on_done=ok, on_fail=fail, on_progress=progress)

    def cancel_job(self):
        """„Abbrechen“: der laufende Auftrag hält nach dem aktuellen Platz an (Erledigtes bleibt)."""
        if self._worker is not None:
            self._worker.cancelled = True
            self.b_cancel.setEnabled(False)
            self.state.setText("Wird nach dem aktuellen Platz angehalten …")
            QTimer.singleShot(0, lambda: self.b_cancel.setEnabled(True))

    def _set_note(self, text: str):
        """Ergebnis einer Aktion merken; read_list() zeigt es vor der Platz-Zusammenfassung an."""
        self._note = text
        self.state.setText(text)

    # ------------------------------------------------------------ Liste
    def read_list(self):
        def work(w, bank):
            return bank.list_all(progress=lambda a, b: w.progress.emit(a, b), cancel=lambda: w.cancelled)

        def done(slots):
            self.slots = slots
            self.cache = {k: v for k, v in self.cache.items() if k < len(slots)}
            used = sum(s["used"] for s in slots)
            fav = sum(s["favorite"] for s in slots)
            note, self._note = self._note, ""
            self.state.setText((note + " — " if note else "")
                               + f"{fw_label(self.ctx.title())}: {used} von {len(slots)} Plätzen belegt"
                               + (f", {fav} Favoriten" if fav else "") + ".")
            self._engines()
            self.fill()
            if getattr(self, "_save_after_read", False):
                self._save_after_read = False
                QTimer.singleShot(0, self.save_all)
        self._job("Preset-Liste lesen", work, done, cancellable=True)

    def _engines(self):
        cur = self.engine.currentData()
        self.engine.blockSignals(True)
        self.engine.clear()
        self.engine.addItem("alle Engines", None)
        engs = sorted({s["engine"] for s in self.slots if s["used"]} | {p.engine for p in self.lib_items})
        for e in engs:
            self.engine.addItem(self._ename(e), e)
        i = self.engine.findData(cur)
        self.engine.setCurrentIndex(max(0, i))
        self.engine.blockSignals(False)

    def _ename(self, e):
        return self.caps.engine_name(e) if self.caps else pl.engine_display_name(e)

    def _match(self, text_parts, engine, fav):
        q = self.search.text().strip().lower()
        e = self.engine.currentData()
        if e is not None and engine != e:
            return False
        if self.only_fav.isChecked() and not fav:
            return False
        return not q or any(q in t.lower() for t in text_parts)

    def fill(self):
        ok = self.ctx.connected
        self.empty.setVisible(not self.slots)
        if ok and self.caps is not None and not self.caps.shared:
            self.empty.setText(pl.refusal_text(self.caps))
        elif not ok and getattr(self.ctx, "midi_connected", False):
            self.empty.setText("Diese Firmware hat keine Preset-Bibliothek, die die App über USB lesen kann "
                               "(nur Melodee ab 1.5). Presets speicherst du hier direkt am FM-1.")
        elif not ok:
            self.empty.setText("Noch kein FM-1 mit Melodee verbunden – so geht's: FM-1 per USB anstecken, "
                               "Melodee 1.5 oder neuer muss laufen.")
        hashes = {p.content_hash() for p in self.lib_items}
        self.dev.setSortingEnabled(False)
        self.dev.clear()
        for s in self.slots:
            if self.only_used.isChecked() and not s["used"]:
                continue
            label = f"U{s['slot'] + 1:03d}"
            eng = self._ename(s["engine"]) if s["used"] else ""
            p = self.cache.get(s["slot"])
            if not self._match([label, s["name"], eng], s["engine"] if s["used"] else -1, s["favorite"]):
                continue
            inlib = "" if not p else ("✓" if p.content_hash() in hashes else "–")
            it = QTreeWidgetItem([label, eng, s["name"] if s["used"] else "(leer)", "★" if s["favorite"] else "",
                                  p.kind_text if p else "", inlib])
            it.setData(0, Qt.ItemDataRole.UserRole, s["slot"])
            self.dev.addTopLevelItem(it)
        self.dev.setSortingEnabled(True)
        self.dev.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.lib.setSortingEnabled(False)
        self.lib.clear()
        for i, p in enumerate(self.lib_items):
            eng = p.engine_name or pl.engine_display_name(p.engine)
            if not self._match([p.name, eng, p.device, p.label, *p.tags], p.engine, p.favorite):
                continue
            it = QTreeWidgetItem([p.name, eng, p.device, p.label, ", ".join(p.tags), p.kind_text])
            it.setData(0, Qt.ItemDataRole.UserRole, i)
            it.setToolTip(0, str(p.path or ""))
            self.lib.addTopLevelItem(it)
        self.lib.setSortingEnabled(True)
        self.lib_empty.setVisible(not self.lib_items)
        for t in (self.dev, self.lib):
            for c in range(t.columnCount()):
                t.resizeColumnToContents(c)
        self._enable()

    def _enable(self):
        ok = self.ctx.connected and not self.busy and bool(self.slots)
        ds, ls = self.sel_slots(), self.sel_lib()
        used = [s for s in ds if self.slots[s]["used"]]
        for b, cond in ((self.b_read, self.ctx.connected and not self.busy), (self.b_all, ok),
                        (self.b_fetch, ok and used), (self.b_load, ok and len(used) == 1),
                        (self.b_store, ok and len(ds) == 1), (self.b_ren, ok and len(used) == 1),
                        (self.b_fav, ok and len(used) == 1 and bool(self.caps and self.caps.favorites)),
                        (self.b_move, ok and len(used) == 1), (self.b_del, ok and bool(used)),
                        (self.b_undo, ok and self.undo.matches(self.ctx)), (self.b_put, ok and len(ls) >= 1 and len(ds) == 1),
                        (self.b_cmp, ok and len(ls) == 1 and len(used) == 1), (self.b_tag, bool(ls)),
                        (self.b_lren, len(ls) == 1), (self.b_exp, bool(ls)), (self.b_ldel, bool(ls))):
            b.setEnabled(bool(cond))
        self.b_undo.setText(f"Rückgängig: {self.undo.what()}" if self.undo else "Rückgängig")
        self.b_undo.setToolTip("letzte Änderung am FM-1 zurücknehmen" if not self.undo or self.undo.matches(self.ctx)
                               else self.undo.refusal())

    def _sel_changed(self):
        self._enable()

    def sel_slots(self) -> list[int]:
        return sorted(it.data(0, Qt.ItemDataRole.UserRole) for it in self.dev.selectedItems())

    def sel_lib(self) -> list[pl.Preset]:
        return [self.lib_items[it.data(0, Qt.ItemDataRole.UserRole)] for it in self.lib.selectedItems()]

    def select_slot(self, slot: int):
        for i in range(self.dev.topLevelItemCount()):
            it = self.dev.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == slot:
                self.dev.setCurrentItem(it)
                return True
        return False

    def refresh_library(self):
        try:
            self.lib_items = self.library().all()
        except OSError as e:
            self.lib_items = []
            self.fs_error = fs_error_text(e, "Bibliothek nicht lesbar")
        if self.fs_error:
            self.lib_empty.setText(self.fs_error)
        self._engines()
        self.fill()

    def _slot_text(self, slot: int) -> str:
        s = self.slots[slot]
        return (f"U{slot + 1:03d} „{s['name']}“ ({self._ename(s['engine'])})" if s["used"]
                else f"U{slot + 1:03d} (leer)")

    # ------------------------------------------------------------ Sichern
    def _fetch(self, slots: list[int], title: str):
        if not slots:
            self.state.setText(f"{title}: Es ist kein belegter Platz gewählt – nichts zu sichern."
                               if self.slots else f"{title}: Erst „Einlesen“, dann sichern.")
            return
        dev = self.device_name()
        favs = {k: self.slots[k]["favorite"] for k in slots}   # im GUI-Thread lesen, nicht im Hintergrund

        def work(w, bank):
            lib = self.library()
            known = lib.hashes()
            new = 0
            got = {}
            for i, k in enumerate(slots):
                if w.cancelled:
                    break
                p = bank.get(k)
                p.device = dev
                p.favorite = favs[k]
                got[k] = p
                if p.used:
                    try:
                        _, n = lib.save(p, dev, known)
                    except OSError as e:
                        raise pl.PresetError(fs_error_text(e, "Preset nicht in der Bibliothek gespeichert")) from e
                    new += n
                w.progress.emit(i + 1, len(slots))
            return got, new

        def done(res):
            got, new = res
            self.cache.update(got)
            stopped = " (abgebrochen)" if len(got) < len(slots) else ""
            self.state.setText(f"{len(got)} von {len(slots)} Presets gelesen{stopped}, {new} neu in der Bibliothek "
                               f"(Ordner Presets/{dev}); {len(got) - new} waren schon da. Am FM-1 wurde nichts geändert.")
            self.refresh_library()
        self._job(title, work, done, cancellable=True, unit="Presets")

    def save_all(self):
        self._fetch([s["slot"] for s in self.slots if s["used"]], "Alle Presets sichern")

    def fetch_selected(self):
        self._fetch([k for k in self.sel_slots() if self.slots[k]["used"]], "Presets holen")

    # ------------------------------------------------------------ Schreiben
    def _after_write(self, slot, old, what):
        self.undo.push(self.ctx, (slot, old), what)
        self.read_list()

    def _keep(self, olds: list, what: str):
        """Rückgängig-Einträge für schon erledigte Schritte anlegen (auch nach einem Fehler mittendrin)."""
        for k, old in olds:
            self.cache.pop(k, None)
            self.undo.push(self.ctx, (k, old), what.format(f"U{k + 1:03d}"), getattr(self, "_job_tag", None))
        olds.clear()

    def write_selected(self):
        ds, ls = self.sel_slots(), self.sel_lib()
        if len(ds) != 1 or not ls:
            return
        start = ds[0]
        targets = list(range(start, min(len(self.slots), start + len(ls))))
        if len(targets) < len(ls):
            show_error(self, "Zu wenig Plätze", f"{len(ls)} Presets passen ab U{start + 1:03d} nicht mehr hinein.")
            return
        occupied = [k for k in targets if self.slots[k]["used"]]
        text = (f"„{ls[0].name}“ in {self._slot_text(start)} schreiben?" if len(ls) == 1 else
                f"{len(ls)} Presets in U{start + 1:03d}–U{targets[-1] + 1:03d} schreiben?")
        if occupied:
            text += "\n\nÜberschrieben wird:\n" + "\n".join(self._slot_text(k) for k in occupied[:12])
            if len(occupied) > 12:
                text += f"\n… und {len(occupied) - 12} weitere"
        text += ("\n\nDer alte Inhalt wird vorher gelesen – du kannst es mit „Rückgängig“ zurücknehmen. "
                 "Das FM-1 stoppt dafür die Wiedergabe.")
        if not confirm(self, "Ins FM-1 schreiben", text, "Schreiben", danger=bool(occupied)):
            return

        olds: list = []                     # wächst mit jedem geschriebenen Platz

        def work(w, bank):
            for i, (k, p) in enumerate(zip(targets, ls)):
                if w.cancelled:
                    break
                old = bank.get(k)
                olds.append((k, old))          # vor dem Schreiben merken (halb geschrieben = auch zurückholen)
                bank.put(k, p)
                w.progress.emit(i + 1, len(ls))
            return len(olds)

        def done(n):
            self._keep(olds, "{} geschrieben")
            if n == 0:                         # vor dem ersten Platz abgebrochen
                self._set_note("Schreiben abgebrochen – am FM-1 wurde nichts geändert.")
                self.read_list()
                return
            stopped = f" ({len(ls) - n} nach dem Abbrechen nicht mehr geschrieben)" if n < len(ls) else ""
            self._set_note(f"{plural(n, 'Preset', 'Presets')} geschrieben{stopped}: "
                           + (f"„{ls[0].name}“ → U{start + 1:03d}" if n == 1 else
                              f"U{start + 1:03d}–U{start + n:03d}")
                           + ". „Rückgängig“ stellt den alten Inhalt wieder her.")
            self.read_list()

        self._job("Presets schreiben", work, done, partial=lambda: self._keep(olds, "{} geschrieben"),
                  cancellable=len(ls) > 1, unit="Presets")

    def do_undo(self):
        if not self.undo or not self.undo.check(self.ctx, self):
            return
        slot, old = self.undo.peek()
        what = self.undo.what()
        if not confirm(self, "Rückgängig", f"{what} zurücknehmen? U{slot + 1:03d} bekommt wieder "
                       + (f"„{old.name}“." if old.used else "den leeren Zustand."), "Zurücknehmen"):
            return

        def work(w, bank):
            bank.restore(slot, old)

        def done(_):
            self.undo.pop()
            self.cache.pop(slot, None)
            self._set_note(f"Zurückgenommen: {what} – U{slot + 1:03d} ist wieder "
                           + (f"„{old.name}“." if old.used else "leer."))
            self.read_list()
        self._job("Rückgängig", work, done)

    def ask_name(self, title: str, prompt: str, current: str, limit: int) -> str | None:
        """Namen erfragen und VOR dem Senden an die Grenzen des FM-1 anpassen (ASCII, limit Zeichen) –
        mit Erklärung und Rückfrage, falls sich der Name dadurch ändert. None = abgebrochen."""
        name, ok = QInputDialog.getText(self, title, prompt, text=current)
        if not ok:
            return None
        fitted, why = pl.fit_name(name, limit)
        if not fitted:
            show_error(self, title, "Der Name darf nicht leer sein" + (f" – {why}" if why else "."))
            return None
        if why and not confirm(self, title, f"{why}\n\nSo übernehmen?", "Übernehmen"):
            return None
        return fitted

    def rename(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if len(ds) != 1:
            return
        k = ds[0]
        limit = pl.name_limit(self.slots[k]["engine"])
        old_name = self.slots[k]["name"]
        name = self.ask_name("Umbenennen", f"Neuer Name für {self._slot_text(k)}\n(höchstens {limit} Zeichen, "
                             "Buchstaben/Ziffern/einfache Zeichen – Umlaute schreibt die App um):", old_name, limit)
        if not name or name == old_name:
            return

        olds: list = []

        def work(w, bank):
            olds.append((k, bank.get(k)))
            bank.rename(k, name)

        def done(_):
            self._keep(olds, "{} umbenannt")
            self._set_note(f"U{k + 1:03d} umbenannt: „{old_name}“ → „{name}“.")
            self.read_list()
        self._job("Umbenennen", work, done, partial=lambda: self._keep(olds, "{} umbenannt"))

    def erase(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if not ds:
            return
        if not confirm(self, "Löschen", f"{plural(len(ds), 'Platz', 'Plätze')} am FM-1 leeren?\n\n"
                       + "\n".join(self._slot_text(k) for k in ds[:12])
                       + (f"\n… und {len(ds) - 12} weitere" if len(ds) > 12 else "")
                       + "\n\nDer Inhalt wird vorher gelesen (Rückgängig möglich).", "Löschen", danger=True):
            return

        olds: list = []
        names = [self.slots[k]["name"] for k in ds]

        def work(w, bank):
            for k in ds:
                if w.cancelled:
                    break
                olds.append((k, bank.get(k)))
                bank.erase(k)
                w.progress.emit(len(olds), len(ds))
            return len(olds)

        def done(n):
            self._keep(olds, "{} gelöscht")
            if n == 0:
                self._set_note("Leeren abgebrochen – am FM-1 wurde nichts geändert.")
                self.read_list()
                return
            self._set_note(f"{plural(n, 'Platz', 'Plätze')} geleert"
                           + (f": U{ds[0] + 1:03d} „{names[0]}“" if n == 1 else "")
                           + (f" ({len(ds) - n} nach dem Abbrechen nicht mehr)" if n < len(ds) else "")
                           + ". „Rückgängig“ holt den Inhalt zurück.")
            self.read_list()
        self._job("Löschen", work, done, partial=lambda: self._keep(olds, "{} gelöscht"), cancellable=len(ds) > 1)

    def toggle_fav(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if len(ds) != 1:
            return
        k = ds[0]
        on = not self.slots[k]["favorite"]

        def done(rc):
            self.slots[k]["favorite"] = on
            what = f"U{k + 1:03d} „{self.slots[k]['name']}“ ist " + ("jetzt Favorit ★" if on else "kein Favorit mehr")
            self.state.setText(what + {0: " – gespeichert.", 3: " – aber nur bis zum Ausschalten (kein Flash).",
                                       4: " – das FM-1 speichert es nach STOP."}.get(rc, "."))
            self.fill()
            self.select_slot(k)
        self._job("Favorit", lambda w, bank: bank.set_favorite(k, on), done)

    def move_slot(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if len(ds) != 1:
            return
        a = ds[0]
        b, ok = QInputDialog.getInt(self, "Verschieben/Tauschen", f"Ziel-Platz für {self._slot_text(a)} (1–384):\n"
                                    "Ist das Ziel belegt, werden die beiden Plätze getauscht.", a + 2, 1, len(self.slots))
        b -= 1
        if not ok or b == a:
            return
        swap = self.slots[b]["used"]
        if not confirm(self, "Verschieben/Tauschen", (f"{self._slot_text(a)} und {self._slot_text(b)} tauschen?" if swap
                       else f"{self._slot_text(a)} nach U{b + 1:03d} verschieben?") +
                       "\n\nBeide alten Inhalte werden vorher gelesen (Rückgängig möglich).", "Ausführen"):
            return

        olds: list = []

        def work(w, bank):
            pa, pb = bank.get(a), bank.get(b)
            olds.extend([(a, pa), (b, pb)])
            bank.put(b, pa, defaults=False)
            if pb.used:
                bank.put(a, pb, defaults=False)
            else:
                bank.erase(a)
            return True

        ta, tb = self._slot_text(a), self._slot_text(b)

        def done(_):
            self._keep(olds, "{} geändert")
            self._set_note(f"Getauscht: {ta} ↔ {tb}." if swap else f"Verschoben: {ta} → U{b + 1:03d} (U{a + 1:03d} ist jetzt leer).")
            self.read_list()
        self._job("Verschieben", work, done, partial=lambda: self._keep(olds, "{} geändert"))

    def load_track(self):
        ds = [k for k in self.sel_slots() if self.slots[k]["used"]]
        if len(ds) != 1:
            return
        k, tr = ds[0], self.track.value()
        self._job("Anhören", lambda w, bank: bank.load(k, tr - 1),
                  lambda _: self.state.setText(f"{self._slot_text(k)} liegt jetzt auf Spur {tr} – spiel ein paar Töne "
                                               "am FM-1. Gespeichert wurde nichts."))

    def store_track(self):
        ds = self.sel_slots()
        if len(ds) != 1:
            return
        k, tr = ds[0], self.track.value()
        name, ok = QInputDialog.getText(self, "Spur speichern", f"Name (leer = automatisch), Klang von Spur {tr} "
                                        f"nach U{k + 1:03d}:", text="")
        if not ok:
            return
        name, why = pl.fit_name(name, 20)
        if why and not confirm(self, "Spur speichern", f"{why}\n\nSo übernehmen?", "Übernehmen"):
            return
        if self.slots[k]["used"] and not confirm(self, "Überschreiben", f"{self._slot_text(k)} wird überschrieben. "
                                                 "Der alte Inhalt wird vorher gelesen (Rückgängig möglich).",
                                                 "Überschreiben", danger=True):
            return

        olds: list = []

        def done(_):
            self._keep(olds, "{} gespeichert")
            self._set_note(f"Klang von Spur {tr} in U{k + 1:03d} gespeichert" + (f" als „{name}“" if name else "")
                           + ". „Rückgängig“ stellt den alten Inhalt wieder her.")
            self.read_list()

        def work(w, bank):
            olds.append((k, bank.get(k)))
            bank.store(k, tr - 1, name)
        self._job("Spur speichern", work, done, partial=lambda: self._keep(olds, "{} gespeichert"))

    # ------------------------------------------------------------ Vergleich
    def compare(self):
        ds, ls = [k for k in self.sel_slots() if self.slots[k]["used"]], self.sel_lib()
        if len(ds) != 1 or len(ls) != 1:
            return
        k, lp = ds[0], ls[0]

        def done(dp):
            self.cache[k] = dp
            lines = pl.diff(dp, lp)
            self.compare_result = lines
            from PyQt6.QtWidgets import QMessageBox
            box = QMessageBox(QMessageBox.Icon.Information, "Vergleich",
                              f"U{k + 1:03d} (FM-1) ↔ „{lp.name}“ (Bibliothek):\n\n" + "\n".join(lines), parent=self)
            box.exec()
            self.fill()
        self._job("Vergleichen", lambda w, bank: bank.get(k), done)

    # ------------------------------------------------------------ Bibliothek
    def edit_tags(self):
        ls = self.sel_lib()
        if not ls:
            return
        cur = ", ".join(sorted({t for p in ls for t in p.tags}))
        text, ok = QInputDialog.getText(self, "Schlagwörter", "Schlagwörter (mit Komma getrennt):", text=cur)
        if not ok:
            return
        tags = [t.strip() for t in text.split(",") if t.strip()]
        lib = self.library()
        try:
            for p in ls:
                p.tags = tags
                lib.update(p)
        except OSError as e:
            show_error(self, "Schlagwörter", fs_error_text(e, "Schlagwörter nicht gespeichert"))
        else:
            self.state.setText(f"Schlagwörter für {plural(len(ls), 'Preset', 'Presets')} gespeichert (nur Bibliothek, am FM-1 nichts geändert).")
        self.refresh_library()

    def import_files(self, files=None):
        if not files:
            files, _ = QFileDialog.getOpenFileNames(self, "Presets importieren", str(Path.home()),
                                                    "Presets (*.fm1preset *.zip *.json *.syx);;Alle Dateien (*)")
        if not files:
            return
        n, errs = 0, []
        lib = self.library()
        known = len(lib.hashes())
        for f in files:
            try:
                n += len(lib.import_file(Path(f)))
            except OSError as e:
                errs.append(f"{Path(f).name}: {fs_error_text(e)}")
            except pl.PresetError as e:
                errs.append(f"{Path(f).name}: {e}")
            except Exception as e:                 # noqa: BLE001 – unerwartet, aber kein Absturz
                errs.append(f"{Path(f).name}: Datei nicht lesbar ({type(e).__name__})")
        new = len(lib.hashes()) - known
        text = (f"{plural(n, 'Preset', 'Presets')} importiert, davon {new} neu in der Bibliothek "
                f"(Ordner Presets/Importiert); {n - new} gab es schon." if n else "Nichts importiert.")
        if errs:
            text += " Nicht lesbar: " + "; ".join(errs)
            show_error(self, "Importieren", f"{len(errs)} Datei(en) konnten nicht gelesen werden:\n\n" + "\n".join(errs)
                       + "\n\nDie Bibliothek wurde dadurch nicht verändert; lesbare Dateien sind übernommen.")
        self.state.setText(text)
        self.refresh_library()

    def lib_rename(self):
        ls = self.sel_lib()
        if len(ls) != 1:
            return
        p = ls[0]
        name = self.ask_name("Umbenennen", f"Neuer Name für „{p.name}“ in der Bibliothek\n(höchstens 20 Zeichen; "
                             "nur die Datei ändert sich, am FM-1 nichts):", p.name, 20)
        if not name or name == p.name:
            return
        old = p.name
        try:
            f = self.library().rename(p, name)
        except (OSError, pl.PresetError) as e:
            show_error(self, "Umbenennen", fs_error_text(e, "Nicht umbenannt") if isinstance(e, OSError) else str(e))
            return
        self.state.setText(f"In der Bibliothek umbenannt: „{old}“ → „{name}“ ({f.name}). Am FM-1 wurde nichts geändert.")
        self.refresh_library()

    def export(self, target=None):
        ls = self.sel_lib()
        if not ls:
            return
        if target is None:
            target, flt = QFileDialog.getSaveFileName(
                self, "Exportieren", str(Path.home() / ("Presets.zip" if len(ls) > 1 else pl.safe_file(ls[0].name) + ".fm1preset")),
                "Satz (*.zip);;Einzeln (*.fm1preset);;Melodee-Web-Editor (*.json)")
        if not target:
            return
        t = Path(target)
        try:
            if t.suffix == ".json":
                t.write_text(json.dumps(pl.to_felucca(ls, self.caps.engines if self.caps else []), indent=1), "utf-8")
            elif t.suffix == ".fm1preset" and len(ls) == 1:
                t.write_text(json.dumps(ls[0].to_json(), indent=1, ensure_ascii=False), "utf-8")
            else:
                t = t.with_suffix(".zip")      # mehrere Presets passen nur in einen Satz
                pl.export_zip(ls, t)
        except OSError as e:
            show_error(self, "Export", fs_error_text(e, "Nicht exportiert"))
            return
        self.state.setText(f"{plural(len(ls), 'Preset', 'Presets')} exportiert: {t.name} – nur als Datei, am FM-1 nichts geändert.")
        notify(f"{plural(len(ls), 'Preset', 'Presets')} exportiert: {t.name}", "success")

    def lib_delete(self):
        ls = self.sel_lib()
        names = "\n".join(f"„{p.name}“ ({p.device or 'ohne Gerät'}{', ' + p.label if p.slot is not None else ''})" for p in ls[:12])
        if ls and confirm(self, "Entfernen", f"{plural(len(ls), 'Preset', 'Presets')} aus der Bibliothek löschen?\n\n{names}"
                          + (f"\n… und {len(ls) - 12} weitere" if len(ls) > 12 else "")
                          + "\n\nDie Dateien werden gelöscht; das FM-1 bleibt unverändert.", "Löschen", danger=True):
            lib = self.library()
            try:
                for p in ls:
                    lib.delete(p)
            except OSError as e:
                show_error(self, "Entfernen", fs_error_text(e, "Nicht gelöscht"))
            else:
                self.state.setText(f"{plural(len(ls), 'Preset', 'Presets')} aus der Bibliothek entfernt – das FM-1 bleibt unverändert.")
            self.refresh_library()

    def open_folder(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root)))


def _make(ctx, window):
    return PresetsPage(ctx, window)


PAGE = PageSpec(key="presets", title="Presets", group="klang", factory=_make,
                icons=("bookmarks", "folder-favorites", "document-open-recent"), order=10,
                description="User-Presets des FM-1 sichern, vergleichen, zurückspielen und anhören",
                keywords=("user", "preset", "bibliothek", "library", "favoriten", "melodee"), needs_device=True)
