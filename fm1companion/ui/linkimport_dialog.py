# SPDX-License-Identifier: GPL-3.0-only
"""Dialoge für den Link-Import: „Link einfügen …" und „Meine Firmwares".

Wiederverwendbar (z. B. von Firmware+ › „Installieren & Versionen“):
    from .linkimport_dialog import LinkImportDialog, OwnFirmwaresDialog
    d = LinkImportDialog(parent, text=clipboard_text)
    if d.exec(): spec = d.saved_spec
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
                             QVBoxLayout, QWidget)

from .. import catalog as catmod
from .. import linkimport as L
from .common import Disclosure, confirm, fs_error_text, icon, notify, run_job

FIELDS = [("name", "line"), ("key", "line"), ("repo", "line"), ("asset", "line"), ("download_url", "line"),
          ("sha256", "line"), ("author", "line"), ("license", "line"), ("category", "combo"),
          ("family", "line"), ("product", "line"), ("description", "text"), ("homepage", "line"),
          ("installer_url", "line"), ("emulator_url", "line"), ("editor_url", "line"), ("manual_url", "line"),
          ("protocol", "proto"), ("requires_stock_v15", "check")]
# Expertenfelder: hinter „Erweitert“ (werden beim Erkennen trotzdem gefüllt und geprüft)
ADVANCED = {"key", "asset", "download_url", "sha256", "family", "product", "protocol", "requires_stock_v15",
            "installer_url", "emulator_url", "editor_url", "manual_url"}
TIPS = {"key": "eindeutiger Kurzname (Ordnername), nur a–z, 0–9, - und _",
        "asset": "Dateiname des Pakets im GitHub-Release; {ver} steht für die Version ohne „v“",
        "download_url": "feste Adresse des .fwsc-Pakets (falls nicht über GitHub-Releases)",
        "sha256": "Prüfsumme des Pakets – die App prüft sie vor dem Aufspielen",
        "protocol": "Felucca-Protokoll = die App kann Backups, Projekte und die Editor-Brücke",
        "requires_stock_v15": "Paket lässt sich nur von der Original-Firmware V15 aus aufspielen"}


def online_allowed() -> bool:
    return not os.environ.get("FM1C_NO_NET")


class LinkImportDialog(QDialog):
    """Link → vorausgefüllter Eintrag mit Markierung „sicher/geraten" → korrigieren → speichern."""

    def __init__(self, parent=None, text: str = "", draft: L.FirmwareDraft | None = None, editing_key: str = "",
                 cat: catmod.Catalog | None = None, online: bool | None = None):
        super().__init__(parent)
        self.cat = cat
        self.editing_key = editing_key
        self.saved_spec = None
        self.draft = draft
        self._gen = 0
        self.setWindowTitle("Firmware bearbeiten" if editing_key else "Link einfügen")
        # passt auch auf den 14"-Laptop (1280×720): nie höher als der nutzbare Bildschirm
        avail = (parent.screen() if parent is not None and parent.screen() else QGuiApplication.primaryScreen())
        geo = avail.availableGeometry() if avail else None
        self.resize(min(720, geo.width() - 40) if geo else 720, min(700, geo.height() - 60) if geo else 700)
        lay = QVBoxLayout(self)
        intro = QLabel("Füge einen Link ein – GitHub-Repo, Release, .fwsc-Paket, Installer- oder Simulator-Seite, "
                       "einen Eintrag von thefirmwarehub.com oder fm1-editor.com, eine lokale .fwsc-Datei oder "
                       "einfach einen Namen. Die App füllt aus, was sie herausfindet; "
                       "<b style='color:#c77700'>geraten</b> markierte Felder bitte prüfen.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        row = QHBoxLayout()
        self.input = QLineEdit(text)
        self.input.setPlaceholderText("https://github.com/besitzer/firmware  oder  ~/Downloads/paket.fwsc  oder  orbit")
        self.input.returnPressed.connect(self.recognize)
        self.b_go = QPushButton(icon("edit-find", "system-search"), "Erkennen")
        self.b_go.clicked.connect(self.recognize)
        self.chk_online = QCheckBox("auf GitHub nachsehen")
        self.chk_online.setToolTip("Releases und Repo-Dateien abfragen (braucht Internet)")
        self.chk_online.setChecked(online_allowed() if online is None else online)
        self.b_pick = QPushButton(icon("view-list-details", "view-list-text"), "Treffer …")
        self.b_pick.setToolTip("Mehrere Firmwares passen zum Namen – die richtige auswählen")
        self.b_pick.clicked.connect(self.choose)
        self.b_pick.hide()
        self.candidates: list[dict] = []
        self._picking = False
        self.chooser = None
        row.addWidget(self.input, 1)
        row.addWidget(self.b_go)
        row.addWidget(self.b_pick)
        row.addWidget(self.chk_online)
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        self.form = QFormLayout()
        body_lay.addLayout(self.form)
        adv = QWidget()
        self.adv_form = QFormLayout(adv)
        self.advanced = Disclosure("Erweitert: Schlüssel, Paketname, Prüfsumme, Familie, Kennung, Protokoll …", adv)
        body_lay.addWidget(self.advanced)
        body_lay.addStretch(1)
        self.w: dict[str, QWidget] = {}
        self.mark: dict[str, QLabel] = {}
        for name, kind in FIELDS:
            if kind == "line":
                w = QLineEdit()
                w.textEdited.connect(lambda _t, n=name: self._edited(n))
            elif kind == "text":
                w = QPlainTextEdit()
                w.setMaximumHeight(80)
                w.textChanged.connect(lambda n=name, ww=w: self._edited(n) if ww.hasFocus() else None)
            elif kind == "combo":
                w = QComboBox()
                w.setEditable(True)
                w.addItems([""] + catmod.CATEGORIES)
                w.currentTextChanged.connect(lambda _t, n=name: self._edited(n) if self.w[n].hasFocus() else None)
            elif kind == "proto":
                w = QComboBox()
                w.addItem("unbekannt – wird beim Verbinden geprüft", "none")
                w.addItem("Felucca-Protokoll (Backup, Projekte, Editor)", "felucca")
                w.activated.connect(lambda _i, n=name: self._edited(n))
            else:
                w = QCheckBox()
                w.clicked.connect(lambda _c, n=name: self._edited(n))
            if name in TIPS:
                w.setToolTip(TIPS[name])
            m = QLabel()
            m.setMinimumWidth(64)
            self.w[name], self.mark[name] = w, m
            h = QHBoxLayout()
            h.addWidget(w, 1)
            h.addWidget(m)
            (self.adv_form if name in ADVANCED else self.form).addRow(L.LABELS.get(name, name) + ":", h)
        lay.addWidget(scroll, 1)
        self.hints = QLabel()
        self.hints.setWordWrap(True)
        self.hints.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.hints)
        self.errors = QLabel()
        self.errors.setWordWrap(True)
        self.errors.setStyleSheet("color:#d9534f;")
        lay.addWidget(self.errors)
        bb = QDialogButtonBox()
        self.b_save = bb.addButton("Zu meinen Firmwares", QDialogButtonBox.ButtonRole.AcceptRole)
        self.b_save.setIcon(icon("list-add", "document-save"))
        bb.addButton("Abbrechen", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if draft is not None:
            self.show_draft(draft)
            self.status.setText("Gespeicherter Eintrag – alle Felder lassen sich ändern.")
        elif text.strip():
            self.recognize()

    # ------------------------------------------------------------ Erkennen
    def recognize(self):
        text = self.input.text()
        self._gen += 1
        gen = self._gen
        if not self._picking:                  # neue Eingabe: alte Trefferliste gilt nicht mehr
            self.candidates = []
        self.show_draft(L.resolve(text, self.cat, online=False))
        # auch bei einem bloßen Namen: Katalog + GitHub-Suche (linkimport._resolve_name)
        if self.chk_online.isChecked() and (self.draft.repo or self.draft.source in ("url", "name")):
            self.status.setText("Sehe auf GitHub nach …")
            run_job(self, lambda w: L.resolve(text, self.cat, online=True),
                    on_done=lambda d: self._online_done(gen, d),
                    on_fail=lambda m, tb: self._online_fail(gen, m))
        else:
            self.status.setText(self._summary())
            self._offer_choice()

    # ------------------------------------------------------------ mehrere Treffer
    def _offer_choice(self):
        """mehrere mögliche Firmwares und keine eindeutig: Auswahlfenster öffnen."""
        d = self.draft
        if not self._picking and d is not None and d.source == "name" and len(self.candidates) > 1 and not d.exact:
            self.choose()

    def choose(self):
        if len(self.candidates) < 2:
            return
        if self.chooser is not None:
            self.chooser.close()
        self.chooser = CandidatesDialog(self, self.candidates, self.draft.repo if self.draft else "")
        self.chooser.accepted.connect(lambda: self._picked(self.chooser.selected()))
        self.chooser.open()

    def _picked(self, cand: dict | None):
        if not cand:
            return
        self._picking = True
        try:
            self.input.setText(cand["input"])
            self.recognize()
        finally:
            self._picking = False

    def _online_done(self, gen, d):
        if gen != self._gen:
            return
        user = {n for n, how in self.draft.confidence.items() if how == "du"}
        keep = {n: self._value(n) for n in user}
        self.show_draft(d)
        for n, v in keep.items():
            self._set_value(n, v)
            self._marker(n, "du")
            self.draft.confidence[n] = "du"
        self.status.setText(self._summary() + " GitHub abgefragt.")
        self._offer_choice()

    def _online_fail(self, gen, msg):
        if gen == self._gen:
            from ..dx7_sources import net_error_text
            self.status.setText(self._summary() + f" GitHub nicht abgefragt: {net_error_text(msg)}")

    def _summary(self) -> str:
        d = self.draft
        if not d or not d.name:
            return ("Nichts erkannt – das sieht nicht nach Link, Datei oder Firmware-Name aus. Beispiele: "
                    "https://github.com/besitzer/firmware, ~/Downloads/paket.fwsc, orbit." if self.input.text().strip()
                    else "Noch nichts erkannt.")
        guessed = [L.LABELS.get(n, n) for n, h in d.confidence.items() if h == L.GUESS and getattr(d, n, "")]
        return f"Erkannt: <b>{d.name}</b>." + (f" Geraten: {', '.join(guessed)}." if guessed else "")

    def show_draft(self, d: L.FirmwareDraft):
        self.draft = d
        if d.candidates and not self._picking:
            self.candidates = list(d.candidates)
        self.b_pick.setText(f"Treffer ({len(self.candidates)}) …")
        self.b_pick.setVisible(len(self.candidates) > 1)
        for name, _kind in FIELDS:
            self._set_value(name, getattr(d, name))
            self._marker(name, d.confidence.get(name, "") if getattr(d, name) not in ("", None, False) else "")
        self.hints.setText("".join(f"• {h}<br>" for h in d.hints))
        self.errors.clear()
        self.status.setText(self._summary())
        # geratene Expertenfelder sollen geprüft werden: dann „Erweitert“ aufklappen
        guessed = [n for n in ADVANCED if d.confidence.get(n) == L.GUESS and getattr(d, n) not in ("", None, False)]
        if guessed or self.editing_key:
            self.advanced.set_open(True)

    def _set_value(self, name, v):
        w = self.w[name]
        w.blockSignals(True)
        if isinstance(w, QLineEdit):
            w.setText(str(v or ""))
        elif isinstance(w, QPlainTextEdit):
            w.setPlainText(str(v or ""))
        elif isinstance(w, QComboBox) and name == "protocol":
            w.setCurrentIndex(max(0, w.findData(v or "none")))
        elif isinstance(w, QComboBox):
            w.setCurrentText(str(v or ""))
        else:
            w.setChecked(bool(v))
        w.blockSignals(False)

    def _value(self, name):
        w = self.w[name]
        if isinstance(w, QLineEdit):
            return w.text().strip()
        if isinstance(w, QPlainTextEdit):
            return w.toPlainText().strip()
        if isinstance(w, QComboBox) and name == "protocol":
            return w.currentData()
        if isinstance(w, QComboBox):
            return w.currentText().strip()
        return w.isChecked()

    def _marker(self, name, how):
        m = self.mark[name]
        style = {L.SURE: ("sicher", "#2e9d4f"), L.GUESS: ("geraten", "#c77700"), "du": ("von dir", "#3daee9")}
        t, col = style.get(how, ("", ""))
        m.setText(f"<span style='color:{col}'>● {t}</span>" if t else "")
        m.setToolTip({"sicher": "aus dem Katalog oder direkt aus dem Link", "geraten": "bitte prüfen",
                      "von dir": "von dir eingetragen"}.get(t, ""))

    def _edited(self, name):
        if self.draft is None:
            self.draft = L.empty_draft()
        self.draft.confidence[name] = "du"
        self._marker(name, "du")

    # ------------------------------------------------------------ Speichern
    def current_draft(self) -> L.FirmwareDraft:
        d = self.draft or L.empty_draft()
        for name, _k in FIELDS:
            setattr(d, name, self._value(name))
        return d

    def _save(self):
        d = self.current_draft()
        errs = L.validate(d, self.editing_key)
        if errs:
            self.errors.setText("<br>".join(errs))
            self.advanced.set_open(True)         # Fehler können Expertenfelder betreffen
            return
        try:
            self.saved_spec = L.save(d, self.editing_key)
        except OSError as e:
            self.errors.setText(fs_error_text(e, "Nicht gespeichert"))
            return
        notify(f"„{getattr(self.saved_spec, 'name', '') or 'Firmware'}“ gespeichert – erscheint unter „Meine Firmwares“. "
               "Installiert wurde nichts.", "success")
        self.accept()


class CandidatesDialog(QDialog):
    """„Welche Firmware meinst du?“ – Treffer aus dem Katalog und der GitHub-Suche zur Auswahl."""

    def __init__(self, parent, candidates: list[dict], current_repo: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Welche Firmware meinst du?")
        self.resize(640, 440)
        self.candidates = candidates
        lay = QVBoxLayout(self)
        intro = QLabel("Zu deiner Eingabe passen mehrere Firmwares. Wähle die richtige aus – die App füllt dann alle "
                       "Angaben dazu aus. <i>Fork</i> heißt: auf GitHub als Abzweig einer anderen Firmware angelegt.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        pre = 0
        for i, c in enumerate(candidates):
            meta = [c["source"]]
            if c.get("stars"):
                meta.append(f"★ {c['stars']}")
            if c.get("fork"):
                meta.append("Fork")
            if c.get("exact"):
                meta.append("Name passt genau")
            it = QListWidgetItem(f"{c['title']}   —   {c['repo'] or 'ohne GitHub-Repo'}\n{' · '.join(meta)}")
            it.setToolTip(c.get("description") or "")
            self.list.addItem(it)
            if c["repo"] and c["repo"].lower() == (current_repo or "").lower():
                pre = i
        self.list.setCurrentRow(pre)
        self.list.itemDoubleClicked.connect(lambda _it: self.accept())
        lay.addWidget(self.list, 1)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setMinimumHeight(48)
        lay.addWidget(self.desc)
        self.list.currentRowChanged.connect(self._show_desc)
        self._show_desc(pre)
        bb = QDialogButtonBox()
        ok = bb.addButton("Diese nehmen", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setIcon(icon("dialog-ok-apply", "dialog-ok"))
        bb.addButton("Abbrechen", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _show_desc(self, row: int):
        c = self.candidates[row] if 0 <= row < len(self.candidates) else None
        self.desc.setText((c.get("description") or "Keine Beschreibung.") if c else "")

    def selected(self) -> dict | None:
        row = self.list.currentRow()
        return self.candidates[row] if 0 <= row < len(self.candidates) else None


class OwnFirmwaresDialog(QDialog):
    """selbst eingetragene Firmwares ansehen, bearbeiten, entfernen."""

    def __init__(self, parent=None, cat=None):
        super().__init__(parent)
        self.cat = cat
        self.changed = False
        self.setWindowTitle("Meine Firmwares")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Firmwares, die du selbst hinzugefügt hast. Sie erscheinen auch in Firmware+ › „Installieren & Versionen“."))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self._edit())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        b_new = QPushButton(icon("edit-paste", "list-add"), "Link einfügen …")
        b_new.clicked.connect(self._new)
        self.b_edit = QPushButton(icon("document-edit", "edit-rename"), "Bearbeiten …")
        self.b_edit.clicked.connect(self._edit)
        self.b_del = QPushButton(icon("list-remove", "edit-delete"), "Entfernen")
        self.b_del.clicked.connect(self._remove)
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        for b in (b_new, self.b_edit, self.b_del):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.fill()

    def fill(self):
        self.list.clear()
        for e in L.own_entries():
            it = QListWidgetItem(icon("application-x-firmware", "cpu"), f"{e.get('name')}   ({e.get('key')})")
            it.setData(Qt.ItemDataRole.UserRole, e.get("key"))
            it.setToolTip(e.get("description") or e.get("repo") or "")
            self.list.addItem(it)
        if not self.list.count():
            it = QListWidgetItem("Noch keine eigenen Firmwares – so geht's: „Link einfügen …“ oder in "
                                 "„Firmware+“ bei einer Firmware „Zu meinen Firmwares“.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(it)
        has = bool(L.own_entries())
        self.b_edit.setEnabled(has)
        self.b_del.setEnabled(has)

    def _key(self) -> str:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else ""

    def _new(self):
        if LinkImportDialog(self, QGuiApplication.clipboard().text() if _looks_like_link(
                QGuiApplication.clipboard().text()) else "", cat=self.cat).exec():
            self.changed = True
            self.fill()

    def _edit(self):
        key = self._key()
        e = next((x for x in L.own_entries() if x.get("key") == key), None)
        if e and LinkImportDialog(self, draft=L.draft_from_saved(e), editing_key=key, cat=self.cat).exec():
            self.changed = True
            self.fill()

    def _remove(self):
        key = self._key()
        if key and confirm(self, "Entfernen", f"„{key}“ aus deinen Firmwares entfernen? Heruntergeladene Pakete "
                                              "bleiben erhalten.", "Entfernen", danger=True):
            L.remove(key)
            self.changed = True
            self.fill()


def _looks_like_link(text: str) -> bool:
    kind, _v = L.extract(text or "")
    return kind in ("url", "file")
