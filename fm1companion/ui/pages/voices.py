# SPDX-License-Identifier: GPL-3.0-only
"""FM-Voices: DX7-Bänke verwalten, Voices anhören (Rechner-Vorschau), ans FM-1 senden, eigene Bänke bauen,
neue Voices erzeugen (Zufall, Mutation, Morph, Variationen)."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout,
                             QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QPushButton, QScrollArea, QSlider, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import config, dx7, dx7_sources, presetlib as pl
from ..common import (DeviceUndo, button, confirm, device_tag, same_device, empty_state, flow_row, fs_error_text, german_buttons,
                      heading, icon, notify, run_job, safe_folder, show_error)
from . import PageSpec

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "H"]


@dataclass
class VoiceRef:
    voice: list
    bank: str              # Anzeigename der Bank
    index: int             # Position in der Bank
    path: Path | None = None

    @property
    def name(self) -> str:
        return dx7.name(self.voice) or "(ohne Namen)"


# ------------------------------------------------------------------ Grafik ---

class AlgoView(QWidget):
    """zeichnet einen der 32 Algorithmen: Träger unten, Modulatoren darüber, Feedback als Schleife."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.alg = 0
        self.fb = 0
        self.setMinimumSize(220, 180)
        self.positions: dict[int, QPointF] = {}

    def set_alg(self, alg: int, fb: int = 0):
        self.alg, self.fb = alg & 31, fb
        self.update()

    def layout_ops(self, w: float, h: float) -> dict[int, QPointF]:
        g = dx7.graph(self.alg)
        mods: dict[int, list[int]] = {}
        parent: dict[int, int] = {}
        for src, dst in g["edges"]:
            if src not in parent:
                parent[src] = dst
                mods.setdefault(dst, []).append(src)
        width: dict[int, int] = {}

        def wid(op):
            if op not in width:
                width[op] = max(1, sum(wid(m) for m in mods.get(op, [])))
            return width[op]
        total = sum(wid(c) for c in g["carriers"])
        depth: dict[int, int] = {}

        def dep(op, d):
            depth[op] = d
            for m in mods.get(op, []):
                dep(m, d + 1)
        for c in g["carriers"]:
            dep(c, 0)
        maxd = max(depth.values()) if depth else 0
        col: dict[int, float] = {}

        def place(op, x0):
            col[op] = x0 + wid(op) / 2
            x = x0
            for m in mods.get(op, []):
                place(m, x)
                x += wid(m)
        x = 0
        for c in g["carriers"]:
            place(c, x)
            x += wid(c)
        sx = w / max(1, total)
        sy = (h - 40) / (maxd + 1)
        return {op: QPointF(col[op] * sx, h - 30 - (depth[op] + 0.5) * sy) for op in col}

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        txt = pal.color(QPalette.ColorRole.Text)
        hl = pal.color(QPalette.ColorRole.Highlight)
        w, h = self.width(), self.height()
        pos = self.positions = self.layout_ops(w, h)
        g = dx7.graph(self.alg)
        ys = sorted({round(c.y()) for c in pos.values()})
        gap = min((b - a for a, b in zip(ys, ys[1:])), default=60)
        r = max(8.0, min(18.0, w / 14, gap * 0.32))
        p.setPen(QPen(txt, 1.5))
        for src, dst in g["edges"]:
            a, b = pos[src], pos[dst]
            p.drawLine(QPointF(a.x(), a.y() + r), QPointF(b.x(), b.y() - r))
        car = g["carriers"]
        if car:
            ybus = h - 14
            xs = [pos[c].x() for c in car]
            p.drawLine(QPointF(min(xs), ybus), QPointF(max(xs), ybus))
            for c in car:
                p.drawLine(QPointF(pos[c].x(), pos[c].y() + r), QPointF(pos[c].x(), ybus))
        if g["feedback"]:
            src, dst = g["feedback"]
            a, b = pos[src], pos[dst]
            pen = QPen(hl, 1.6 if self.fb else 1.0, Qt.PenStyle.SolidLine if self.fb else Qt.PenStyle.DashLine)
            p.setPen(pen)
            path = QPainterPath(QPointF(a.x() + r, a.y()))
            xr = max(a.x(), b.x()) + r + 12
            path.lineTo(QPointF(xr, a.y()))
            path.lineTo(QPointF(xr, b.y() - r - 8))
            path.lineTo(QPointF(b.x(), b.y() - r - 8))
            path.lineTo(QPointF(b.x(), b.y() - r))
            p.drawPath(path)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        for op, c in pos.items():
            box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            p.setPen(QPen(hl if op in car else txt, 1.6))
            p.setBrush(pal.color(QPalette.ColorRole.Window))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(txt)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(op))
        p.setPen(txt)
        p.setFont(self.font())
        p.drawText(6, 14, f"Algorithmus {self.alg + 1}" + (f" · Feedback {self.fb}" if self.fb else ""))


class EnvView(QWidget):
    """die sechs Operator-Hüllkurven (Zeit grob aus den Raten, Pegel aus den Levels)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.voice = None
        self.setMinimumHeight(110)

    def set_voice(self, v):
        self.voice = v
        self.update()

    @staticmethod
    def seg_time(rate: int, dl: int) -> float:
        return (abs(dl) / 99 + 0.05) * 2.0 ** ((99 - rate) / 12) * 0.01

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        if not self.voice:
            return
        w, h = self.width(), self.height()
        car = set(dx7.carriers(self.voice[dx7.VI["ALG"]]))
        curves = []
        tmax = 0.1
        for op in range(1, 7):
            R = [self.voice[dx7.at(op, f"R{i}")] for i in range(1, 5)]
            L = [self.voice[dx7.at(op, f"L{i}")] for i in range(1, 5)]
            pts, t, lv = [(0.0, L[3])], 0.0, L[3]
            for k in range(3):
                t += self.seg_time(R[k], L[k] - lv)
                lv = L[k]
                pts.append((t, lv))
            t += 0.25
            pts.append((t, lv))
            t += self.seg_time(R[3], L[3] - lv)
            pts.append((t, L[3]))
            tmax = max(tmax, t)
            curves.append((op, pts, self.voice[dx7.at(op, "OL")]))
        colors = ["#e5534b", "#e0823d", "#c9b03a", "#57ab5a", "#539bf5", "#b083f0"]
        for op, pts, ol in curves:
            c = QColor(colors[op - 1])
            if op not in car:
                c.setAlpha(140)
            p.setPen(QPen(c, 2.0 if op in car else 1.2))
            path = QPainterPath()
            for i, (t, lv) in enumerate(pts):
                pt = QPointF(4 + t / tmax * (w - 8), h - 6 - lv / 99 * (h - 20) * (0.4 + 0.6 * ol / 99))
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.drawPath(path)
        p.setPen(pal.color(QPalette.ColorRole.Text))
        p.drawText(6, 14, "Hüllkurven (Träger kräftig)")


# ------------------------------------------------------------------ Seite ---

class VoicesPage(QWidget):
    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.banks: dict[str, list[VoiceRef]] = {}
        self.current: VoiceRef | None = None
        self.partner: VoiceRef | None = None
        self.undo = DeviceUndo()           # (Platz, altes Preset) je Gerät
        self.fs_error = ""
        self.player = None
        self.render_seq = 0
        self.last_render = None
        lay = QVBoxLayout(self)
        lay.addWidget(heading("FM-Voices", "DX7-Voices für die FM6-Engine von Melodee und jedes FM-1: Bänke "
                                           "sammeln, im Rechner anhören, ans FM-1 schicken, eigene Bänke bauen und "
                                           "neue Klänge erzeugen. Ordner: FM-1/DX7 (wird beobachtet)."))
        self.state = QLabel("")
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suche über alle Bänke: Name, Bank, „alg 5“, Schlagwort …  (Strg+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search.setFocus)
        tb = []
        for text, ic, fn, tip in (("Importieren …", "document-import", self.import_files, ".syx-Dateien in FM-1/DX7 kopieren"),
                                  ("Ordner …", "folder-open", self.import_folder, "alle .syx eines Ordners übernehmen"),
                                  ("Sammlungen …", "download", self.collections, "freie DX7-Sammlungen von GitHub laden")):
            b = QPushButton(icon(ic), text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            tb.append(b)
        self.search.setMinimumWidth(220)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(flow_row(*tb))
        lay.addLayout(top)

        sp = QSplitter(Qt.Orientation.Horizontal)
        # Leerzustand (keine Bänke): Einstieg statt leerer Tabellen
        self.stack = QStackedWidget()
        self.start = empty_state("audio-lines", "Noch keine DX7-Voices",
                                 "DX7-Klänge für die FM6-Engine: „Sammlungen“ lädt freie Bänke von GitHub (z. B. die 128 "
                                 "Werks-Voices), „Importieren“ übernimmt eigene .syx-Dateien nach FM-1/DX7.",
                                 "Sammlungen laden …", self.collections)
        b_imp0 = button("Eigene .syx-Dateien importieren …", "document-import", slot=self.import_files)
        self.start.layout().addWidget(b_imp0, 0, Qt.AlignmentFlag.AlignHCenter)
        self.stack.addWidget(self.start)
        self.stack.addWidget(sp)
        lay.addWidget(self.stack, 1)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.bank_list = QListWidget()
        self.bank_list.setMaximumHeight(140)
        self.bank_list.currentRowChanged.connect(lambda _: self.fill())
        ll.addWidget(QLabel("<b>Bänke</b>"))
        ll.addWidget(self.bank_list)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(["Voice", "Bank", "Nr.", "Alg."])
        self.table.setRootIsDecorated(False)
        self.table.setSortingEnabled(True)
        self.table.setDragEnabled(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.currentItemChanged.connect(self._pick)
        self.table.itemDoubleClicked.connect(lambda *_: self.preview())
        ll.addWidget(self.table, 1)
        self.empty = QLabel("Noch keine DX7-Bänke – so geht's: „Sammlungen …“ lädt z. B. die 128 Werks-Voices des "
                            "FM-1, oder „Importieren …“ übernimmt deine .syx-Dateien.")
        self.empty.setWordWrap(True)
        ll.addWidget(self.empty)
        sp.addWidget(left)

        # ---- Detail
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        right = QWidget()
        scroll.setWidget(right)
        rl = QVBoxLayout(right)
        self.title = QLabel("Keine Voice gewählt")
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        f.setBold(True)
        self.title.setFont(f)
        rl.addWidget(self.title)
        row = QHBoxLayout()
        self.algo = AlgoView()
        row.addWidget(self.algo, 1)
        self.env = EnvView()
        row.addWidget(self.env, 1)
        rl.addLayout(row)
        self.ops = QTableWidget(6, 9)
        self.ops.setHorizontalHeaderLabels(["Op", "Modus", "Frequenz", "Verst.", "Pegel", "Raten", "Levels", "Dyn.", "Rolle"])
        self.ops.verticalHeader().setVisible(False)
        self.ops.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ops.setMinimumHeight(200)
        rl.addWidget(self.ops)
        # Vorschau
        pv = QGroupBox("Anhören (im Rechner berechnet – ähnlich, nicht identisch mit dem FM-1)")
        pl_ = QVBoxLayout(pv)
        hk = []
        self.b_play = QPushButton(icon("media-playback-start"), "Anhören")
        self.b_play.setToolTip("C4 zwei Sekunden lang (Leertaste)")
        self.b_play.clicked.connect(lambda: self.preview())
        QShortcut(QKeySequence("Space"), self.table, activated=lambda: self.preview())
        hk.append(self.b_play)
        self.keys = []
        for n in range(60, 73):
            if NOTE_NAMES[n % 12].endswith("#"):
                continue
            b = QPushButton(f"{NOTE_NAMES[n % 12]}{n // 12 - 1}")
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(b.text()) + 22)
            b.setToolTip(f"Note {n} vorhören")
            b.clicked.connect(lambda _=False, n=n: self.preview(note=n))
            hk.append(b)
            self.keys.append(b)
        from ..audio import OutputButton, Waveform
        hk.append(OutputButton(self))
        pl_.addWidget(flow_row(*hk, hspacing=4))
        self.wave = Waveform()
        pl_.addWidget(self.wave)
        rl.addWidget(pv)
        # Senden / Export
        sd = QGroupBox("Ans FM-1 und in Dateien")
        sl = QVBoxLayout(sd)
        self.b_send = QPushButton(icon("document-send"), "Ans FM-1 …")
        self.b_send.setToolTip("Melodee: in einen User-Platz speichern oder live auf eine Spur; andere Firmware: als DX7-SysEx")
        self.b_send.clicked.connect(self.send)
        self.b_undo = QPushButton(icon("edit-undo"), "Rückgängig")
        self.b_undo.clicked.connect(self.do_undo)
        self.b_undo.setEnabled(False)
        b_exp = QPushButton(icon("document-export"), "Als .syx …")
        b_exp.setToolTip("diese Voice als Einzel-Voice-SysEx speichern")
        b_exp.clicked.connect(lambda: self.export_voice())
        b_add = QPushButton(icon("list-add"), "Zur eigenen Bank")
        b_add.clicked.connect(self.add_to_own)
        sl.addWidget(flow_row(self.b_send, self.b_undo, b_exp, b_add))
        rl.addWidget(sd)
        # Generatoren
        gb = QGroupBox("Neue Klänge erzeugen")
        gl = QGridLayout(gb)
        b_rand = QPushButton(icon("roll"), "Zufalls-Voice")
        b_rand.setToolTip("musikalisch eingeschränkter Zufall (hörbare Träger, sinnvolle Hüllkurven)")
        b_rand.clicked.connect(self.gen_random)
        gl.addWidget(b_rand, 0, 0)
        self.amount = QSlider(Qt.Orientation.Horizontal)
        self.amount.setRange(5, 100)
        self.amount.setValue(30)
        self.amount.setToolTip("Stärke der Mutation")
        self.keep_alg = QCheckBox("Algorithmus behalten")
        self.keep_alg.setChecked(True)
        b_mut = QPushButton(icon("tools-wizard"), "Mutieren")
        b_mut.clicked.connect(self.gen_mutate)
        gl.addWidget(QLabel("Stärke:"), 1, 0)
        gl.addWidget(self.amount, 1, 1)
        gl.addWidget(self.keep_alg, 1, 2)
        gl.addWidget(b_mut, 1, 3)
        b_partner = QPushButton("Als Morph-Partner merken")
        b_partner.clicked.connect(self.set_partner)
        self.morph = QSlider(Qt.Orientation.Horizontal)
        self.morph.setRange(0, 100)
        self.morph.setValue(50)
        b_morph = QPushButton("Morphen")
        b_morph.clicked.connect(self.gen_morph)
        self.partner_lab = QLabel("Partner: –")
        gl.addWidget(b_partner, 2, 0)
        gl.addWidget(self.morph, 2, 1)
        gl.addWidget(self.partner_lab, 2, 2)
        gl.addWidget(b_morph, 2, 3)
        b_var = QPushButton(icon("view-list-icons"), "Variationen erzeugen")
        b_var.setToolTip("8 Abwandlungen der Voice – anhören (Doppelklick) und die guten behalten")
        b_var.clicked.connect(self.gen_variations)
        gl.addWidget(b_var, 3, 0)
        self.children = QListWidget()
        self.children.setMaximumHeight(110)
        self.children.setFlow(QListWidget.Flow.LeftToRight)
        self.children.setWrapping(True)
        self.children.itemDoubleClicked.connect(self._child_play)
        self.children.currentRowChanged.connect(self._child_pick)
        gl.addWidget(self.children, 4, 0, 1, 3)
        b_keep = QPushButton(icon("dialog-ok"), "Behalten")
        b_keep.setToolTip("gewählte Variation in die eigene Bank übernehmen")
        b_keep.clicked.connect(self.keep_child)
        gl.addWidget(b_keep, 4, 3)
        rl.addWidget(gb)
        # Eigene Bank
        ob = QGroupBox("Eigene Bank (32 Plätze – Voices hierher ziehen)")
        ol = QVBoxLayout(ob)
        self.own = OwnBank(self)
        ol.addWidget(self.own)
        obs = []
        for text, fn in (("Entfernen", self.own.remove_selected), ("Leeren", self.own.clear_all),
                         ("Bank speichern (.syx) …", lambda: self.save_own())):   # (clicked liefert False als target)
            b = QPushButton(text)
            b.clicked.connect(fn)
            obs.append(b)
        ol.addWidget(flow_row(*obs))
        rl.addWidget(ob)
        rl.addStretch(1)
        sp.addWidget(scroll)
        sp.setSizes([420, 700])

        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _: QTimer.singleShot(400, self.scan))
        ctx.changed.connect(self._dev_changed)
        self.scan()

    def deactivate(self):
        """Seite verlassen: Vorschau anhalten."""
        if self.player is not None:
            self.player.stop()

    def shutdown(self):
        self.deactivate()

    def _dev_changed(self):
        self.b_undo.setEnabled(bool(self.undo))
        self.b_undo.setToolTip("" if not self.undo or self.undo.matches(self.ctx) else self.undo.refusal())

    # ------------------------------------------------------------ Bänke
    @property
    def root(self) -> Path:
        p, self.fs_error = safe_folder("DX7")
        return p

    def _files(self, root: Path) -> list[Path]:
        try:
            return sorted(root.rglob("*")) if root.is_dir() else []
        except OSError:
            return []

    def scan(self):
        root = self.root
        banks: dict[str, list[VoiceRef]] = {}
        dirs = [str(root)] if root.is_dir() else []
        if self.fs_error:
            self.state.setText(self.fs_error + " Anhören und Erzeugen geht trotzdem.")
        for f in self._files(root):
            if f.is_dir():
                dirs.append(str(f))
            if f.suffix.lower() != ".syx" or not f.is_file():
                continue
            try:
                res = dx7.parse_syx(f.read_bytes())
            except OSError:
                continue
            if not res.voices:
                continue
            rel = f.relative_to(root)
            label = f"{rel.parent.name + ' / ' if rel.parent.name and rel.parent.name != 'Sammlungen' else ''}{f.stem}"
            banks[label] = [VoiceRef(v, label, i, f) for i, v in enumerate(res.voices)]
        for k in ("Erzeugt",):
            if k in self.banks:
                banks[k] = self.banks[k]
        self.banks = banks
        old = self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        if dirs:
            self.watcher.addPaths(dirs[:200])
        cur = self.bank_list.currentItem().text() if self.bank_list.currentItem() else ""
        self.bank_list.blockSignals(True)
        self.bank_list.clear()
        self.bank_list.addItem(f"Alle Bänke ({sum(len(v) for v in banks.values())} Voices)")
        for k in banks:
            self.bank_list.addItem(k)
        items = self.bank_list.findItems(cur, Qt.MatchFlag.MatchExactly) if cur else []
        self.bank_list.setCurrentRow(self.bank_list.row(items[0]) if items else 0)
        self.bank_list.blockSignals(False)
        self.fill()

    def all_refs(self) -> list[VoiceRef]:
        row = self.bank_list.currentRow()
        if row <= 0:
            return [r for v in self.banks.values() for r in v]
        key = self.bank_list.item(row).text()
        return list(self.banks.get(key, []))

    def fill(self):
        q = self.search.text().strip().lower()
        self.table.setSortingEnabled(False)
        self.table.clear()
        shown = 0
        for ref in self.all_refs():
            alg = ref.voice[dx7.VI["ALG"]] + 1
            hay = f"{ref.name} {ref.bank} alg {alg} alg{alg}".lower()
            if q and not all(part in hay for part in q.split()):
                continue
            it = QTreeWidgetItem([ref.name, ref.bank, f"{ref.index + 1:02d}", str(alg)])
            it.setData(0, Qt.ItemDataRole.UserRole, ref)
            self.table.addTopLevelItem(it)
            shown += 1
            if shown >= 5000:
                break
        self.table.setSortingEnabled(True)
        self.empty.setVisible(not self.banks)
        self.stack.setCurrentIndex(0 if not self.banks else 1)
        self.table.resizeColumnToContents(0)

    def _pick(self, it, _prev=None):
        if it is None:
            return
        self.show_voice(it.data(0, Qt.ItemDataRole.UserRole))

    def show_voice(self, ref: VoiceRef):
        self.current = ref
        v = ref.voice
        self.title.setText(f"{ref.name}  ·  {ref.bank} #{ref.index + 1}")
        self.algo.set_alg(v[dx7.VI["ALG"]], v[dx7.VI["FB"]])
        self.env.set_voice(v)
        car = set(dx7.carriers(v[dx7.VI["ALG"]]))
        for row, op in enumerate(range(1, 7)):
            fixed = v[dx7.at(op, "MODE")] == 1
            fc, ff = v[dx7.at(op, "FC")], v[dx7.at(op, "FF")]
            freq = (f"{10 ** ((fc & 3) + ff / 100):.0f} Hz" if fixed else f"× {(fc or 0.5) * (1 + ff / 100):.2f}")
            vals = [str(op), "fest" if fixed else "Verhältnis", freq, f"{v[dx7.at(op, 'DET')] - 7:+d}",
                    str(v[dx7.at(op, "OL")]),
                    " ".join(str(v[dx7.at(op, f"R{i}")]) for i in range(1, 5)),
                    " ".join(str(v[dx7.at(op, f"L{i}")]) for i in range(1, 5)),
                    str(v[dx7.at(op, "KVS")]), "Träger" if op in car else "Modulator"]
            for c, t in enumerate(vals):
                self.ops.setItem(row, c, QTableWidgetItem(t))
        self.ops.resizeColumnsToContents()

    # ------------------------------------------------------------ Import
    def import_files(self, files=None):
        if not files:
            files, _ = QFileDialog.getOpenFileNames(self, "DX7-Bänke importieren", str(Path.home()),
                                                    "DX7-SysEx (*.syx *.SYX);;Alle Dateien (*)")
        n, bad = 0, []
        target = self.root / "Importiert"
        if files:
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                show_error(self, "Importieren", fs_error_text(e, "Ordner FM-1/DX7 nicht beschreibbar"))
                return
        for f in files or []:
            f = Path(f)
            try:
                data = f.read_bytes()
                res = dx7.parse_syx(data)
            except OSError as e:
                bad.append(f"{f.name}: {fs_error_text(e)}")
                continue
            if not res.voices:
                bad.append(f"{f.name}: keine DX7-Voices")
                continue
            dst = target / (f.stem + ".syx")
            try:
                if dst.resolve() != f.resolve():
                    dst.write_bytes(data)
            except OSError as e:
                bad.append(f"{f.name}: {fs_error_text(e)}")
                continue
            n += 1
            if res.warnings:
                bad.append(f"{f.name}: " + " ".join(res.warnings[:2]))
        self.scan()
        if files:
            self.state.setText(f"{n} Datei(en) übernommen." + (" Hinweise: " + " · ".join(bad) if bad else ""))

    def import_folder(self, folder=None):
        if not folder:
            folder = QFileDialog.getExistingDirectory(self, "Ordner mit .syx-Dateien", str(Path.home()))
        if folder:
            self.import_files([str(p) for p in Path(folder).rglob("*") if p.suffix.lower() == ".syx"])

    def collections(self):
        d = CollectionsDialog(self)
        d.exec()
        self.scan()

    # ------------------------------------------------------------ Anhören
    def preview(self, note: int = 60, voice=None, play: bool = True):
        v = voice if voice is not None else (self.current.voice if self.current else None)
        if v is None:
            return
        self.render_seq += 1
        seq = self.render_seq
        from ... import fmrender

        def work(w):
            t0 = time.time()
            x = fmrender.render(v, note=note, seconds=2.0)
            d = config.CACHE_DIR / "klang"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"vorschau-{seq % 4}.wav"
            fmrender.write_wav(x, path)
            return x, path, time.time() - t0

        def done(res):
            x, path, secs = res
            if seq != self.render_seq:
                return
            self.last_render = (x, path, secs)
            step = max(1, len(x) // 600)
            blocks = x[: len(x) // step * step].reshape(-1, step)
            self.wave.set_data(list(zip(blocks.min(axis=1).tolist(), blocks.max(axis=1).tolist())), len(x) / 44100)
            if play:
                if self.player is None:
                    from ..audio import Player
                    self.player = Player(self)
                if not self.player.play(path, status=self.state.setText):
                    return
            self.state.setText(f"Vorschau berechnet in {secs:.2f} s.")
        run_job(self, work, on_done=done, on_fail=lambda m, tb: self.state.setText(f"Vorschau fehlgeschlagen: {m}"))

    # ------------------------------------------------------------ Senden
    def send(self):
        if not self.current:
            return
        v = self.current.voice
        ctx = self.ctx
        midi = getattr(ctx, "midi_connected", ctx.link is not None)
        if not midi:
            show_error(self, "Kein Gerät", "Bitte ein FM-1 per USB verbinden.")
            return
        fw = ctx.firmware_key or ""
        options = []
        if ctx.connected and fw == "melodee":
            options += ["In einen User-Platz speichern (FM6-Preset) …", "Live auf eine Spur laden (nicht gespeichert)"]
        options.append("Als DX7-SysEx senden")
        item, ok = QInputDialog.getItem(self, "Ans FM-1", f"„{self.current.name}“ – wie?", options, 0, False)
        if not ok:
            return
        if item.startswith("In einen User-Platz"):
            self.send_to_slot(v)
        elif item.startswith("Live"):
            tr, ok = QInputDialog.getInt(self, "Spur", "Auf welche Spur (1–4)?", 1, 1, 4)
            if ok:
                self._melodee_job("Voice auf Spur laden",
                                  lambda w, bank: bank.voice_to_track(tr - 1, bytes(dx7.pack(v))),
                                  lambda _: self.state.setText(f"„{self.current.name}“ spielt jetzt auf Spur {tr} "
                                                               "(FM6). Gespeichert wurde nichts."))
        else:
            self.send_sysex(v)

    def _melodee_job(self, title, fn, done):
        def work(w, link):
            caps = pl.probe(link, self.ctx.info)
            if not caps.shared:
                raise pl.PresetError(pl.refusal_text(caps))
            return fn(w, pl.SharedBank(link, caps))

        def fail(m, tb):
            if m in (pl.refusal_text(pl.Caps()), pl.refusal_text(pl.Caps(legacy=True))):
                # keine Panne, sondern eine Eigenschaft der Firmware: ruhig erklären
                self.state.setText(f"Auf diesem FM-1 nicht möglich – am FM-1 wurde nichts geändert. {m}")
                show_error(self, "Auf diesem FM-1 nicht möglich", m + "\n\nAm FM-1 wurde nichts geändert.")
                return
            self.state.setText(f"{title} fehlgeschlagen: {m} Am FM-1 wurde nichts geändert.")
            show_error(self, title, m, tb)
        self.state.setText(f"{title} …")
        self.ctx.job(self, title, work, dialog=False, on_done=done, on_fail=fail,
                     on_progress=lambda a, b: self.state.setText(f"{title} … {a} von {b}"))

    def send_to_slot(self, v, slot: int | None = None):
        def find(w, bank):
            return bank.list_all(progress=lambda a, b: w.progress.emit(a, b))

        tag0 = device_tag(self.ctx)

        def gone() -> bool:
            """Gerät gewechselt, seit die Plätze gelesen wurden? Dann nicht schreiben – die Belegung stimmt nicht mehr."""
            if same_device(tag0, device_tag(self.ctx)):
                return False
            self.state.setText("Das FM-1 wurde gewechselt, während du den Platz gewählt hast – nichts gespeichert. "
                               "Bitte noch einmal „Ans FM-1“.")
            return True

        def chosen(slots):
            self.state.setText("")
            if gone():
                return
            free = next((s["slot"] for s in slots if not s["used"]), 0)
            k = slot
            if k is None:
                k, ok = QInputDialog.getInt(self, "User-Platz", "In welchen Platz (1–384)? Vorgeschlagen: der erste freie.",
                                            free + 1, 1, len(slots))
                if not ok:
                    return
                k -= 1
            s = slots[k]
            what = (f"U{k + 1:03d} „{s['name']}“ ({pl.engine_display_name(s['engine'])}) wird überschrieben."
                    if s["used"] else f"U{k + 1:03d} ist leer.")
            if not confirm(self, "Als FM6-Preset speichern",
                           f"„{dx7.name(v)}“ als FM6-Preset in U{k + 1:03d} speichern?\n\n{what}\n\nDer alte Inhalt wird "
                           "vorher gelesen (Rückgängig möglich). Das FM-1 stoppt dafür die Wiedergabe.",
                           "Speichern", danger=bool(s["used"])) or gone():
                return
            p = pl.preset_from_voice(v)
            tag = device_tag(self.ctx)

            def write(w, bank):
                return bank.write_with_undo(k, p)

            def done(old):
                self.undo.push(self.ctx, (k, old), f"U{k + 1:03d}", tag)
                self._dev_changed()
                self.state.setText(f"„{dx7.name(v)}“ liegt jetzt in U{k + 1:03d} (FM6). „Rückgängig“ stellt den alten Inhalt her.")
            self._melodee_job("FM6-Preset speichern", write, done)
        self._melodee_job("User-Plätze lesen", find, chosen)

    def do_undo(self):
        if not self.undo or not self.undo.check(self.ctx, self):
            return
        k, old = self.undo.peek()
        if not confirm(self, "Rückgängig", f"U{k + 1:03d} wieder auf " + (f"„{old.name}“" if old.used else "leer") + " setzen?",
                       "Zurücknehmen"):
            return

        def done(_):
            self.undo.pop()
            self._dev_changed()
            self.state.setText(f"U{k + 1:03d} zurückgesetzt.")
        self._melodee_job("Rückgängig", lambda w, bank: bank.restore(k, old), done)

    def send_sysex(self, v):
        ident = getattr(self.ctx, "identity", "") or (self.ctx.profile.identity if self.ctx.profile else "")
        plan = dx7.send_plan(self.ctx.firmware_key, ident)
        ch, ok = QInputDialog.getInt(self, "MIDI-Kanal", "Auf welchem MIDI-Kanal (1–16)?", 1, 1, 16)
        if not ok:
            return
        if plan == "single":
            text = ("Einzel-Voice-Dump senden. Je nach Firmware ersetzt er den Klang der passenden FM-Spur "
                    "(Melodee: die FM6-Spur; M-VAVE-Firmware: das gewählte Preset – zum Behalten am Gerät SAVE halten).")
            msgs = [dx7.single_syx(v, ch - 1)]
        else:
            text = ("Diese Firmware ist nicht sicher bekannt: die Voice geht als 155 einzelne DX7-Parameteränderungen "
                    "raus. Das ändert nur den aktuellen Klang und überschreibt kein gespeichertes Preset.")
            msgs = dx7.param_changes(v, ch - 1)
        if not confirm(self, "DX7-SysEx senden", f"„{dx7.name(v)}“ auf Kanal {ch}: {text}", "Senden"):
            return
        link = self.ctx.link
        sent = sum(1 for m in msgs if link and link.send_midi(list(m)))
        self.state.setText(f"{sent} SysEx-Nachricht(en) gesendet." if sent == len(msgs)
                           else "Senden nicht möglich – das Gerät ist gerade beschäftigt.")

    # ------------------------------------------------------------ Export / eigene Bank
    def export_voice(self, target=None):
        if not self.current:
            return
        if target is None:
            target, _ = QFileDialog.getSaveFileName(self, "Voice speichern", str(Path.home() / (pl.safe_file(self.current.name) + ".syx")),
                                                    "DX7-SysEx (*.syx)")
        if target:
            try:
                Path(target).write_bytes(dx7.single_syx(self.current.voice))
            except OSError as e:
                show_error(self, "Voice speichern", fs_error_text(e, "Nicht gespeichert"))
                return
            self.state.setText(f"Gespeichert: {Path(target).name}")

    def add_to_own(self):
        refs = [it.data(0, Qt.ItemDataRole.UserRole) for it in self.table.selectedItems()] or ([self.current] if self.current else [])
        for r in refs:
            self.own.add(r.voice)
        if refs:
            self.state.setText(f"{len(refs)} Voice(s) zur eigenen Bank hinzugefügt – „Bank speichern“ legt die .syx an.")

    def save_own(self, target=None):
        if not self.own.voices():
            show_error(self, "Eigene Bank", "Die eigene Bank ist leer – zieh Voices aus der Liste hierher.")
            return
        if target is None:
            name, ok = QInputDialog.getText(self, "Bank speichern", "Name der Bank:", text="Meine Bank")
            if not ok or not name.strip():
                return
            target = self.root / "Eigene Bänke" / (pl.safe_file(name) + ".syx")
        target = Path(target)
        vs = self.own.voices()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(dx7.bank_syx(vs))
        except OSError as e:
            show_error(self, "Bank speichern", fs_error_text(e, "Nicht gespeichert"))
            return
        self.state.setText(f"Bank gespeichert: {target.name} ({len(vs)} Voices, Rest INIT VOICE).")
        self.scan()

    # ------------------------------------------------------------ Generatoren
    def _generated(self, v, label="Erzeugt") -> VoiceRef:
        lst = self.banks.setdefault("Erzeugt", [])
        ref = VoiceRef(v, "Erzeugt", len(lst))
        lst.append(ref)
        if not self.bank_list.findItems("Erzeugt", Qt.MatchFlag.MatchExactly):
            self.bank_list.addItem("Erzeugt")
        self.fill()
        self.show_voice(ref)
        return ref

    def gen_random(self):
        self._generated(dx7.random_voice())
        self.preview()

    def gen_mutate(self):
        if self.current:
            v = dx7.mutate(self.current.voice, self.amount.value() / 100, self.keep_alg.isChecked())
            self._generated(dx7.set_name(v, (dx7.name(self.current.voice)[:8] + " M").strip()))
            self.preview()

    def set_partner(self):
        if self.current:
            self.partner = self.current
            self.partner_lab.setText(f"Partner: {self.current.name}")

    def gen_morph(self):
        if not (self.current and self.partner):
            show_error(self, "Morphen", "Erst eine Voice als Morph-Partner merken, dann eine zweite wählen.")
            return
        t = self.morph.value() / 100
        v = dx7.morph(self.current.voice, self.partner.voice, t)
        self._generated(dx7.set_name(v, f"MORPH {int(t * 100):02d}"))
        self.preview()

    def gen_variations(self):
        if not self.current:
            return
        self.children.clear()
        for v in dx7.variations(self.current.voice, 8, self.amount.value() / 100, seed=random.randrange(1 << 30)):
            it = QListWidgetItem(dx7.name(v))
            it.setData(Qt.ItemDataRole.UserRole, v)
            self.children.addItem(it)

    def _child_pick(self, row):
        it = self.children.item(row) if row >= 0 else None
        if it is not None:
            self.algo.set_alg(it.data(Qt.ItemDataRole.UserRole)[dx7.VI["ALG"]])

    def _child_play(self, it):
        self.preview(voice=it.data(Qt.ItemDataRole.UserRole))

    def keep_child(self):
        it = self.children.currentItem()
        if it:
            v = it.data(Qt.ItemDataRole.UserRole)
            self._generated(v)
            self.own.add(v)
            self.state.setText(f"„{dx7.name(v)}“ behalten (Bank „Erzeugt“ und eigene Bank).")


class OwnBank(QListWidget):
    """bis zu 32 Voices; Drag & Drop aus der Voice-Liste, Reihenfolge per Ziehen."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setMinimumHeight(120)
        self.itemDoubleClicked.connect(lambda it: page.preview(voice=it.data(Qt.ItemDataRole.UserRole)))

    def add(self, v) -> bool:
        if self.count() >= 32:
            self.page.state.setText("Die eigene Bank ist voll (32 Voices).")
            return False
        it = QListWidgetItem(f"{self.count() + 1:02d} {dx7.name(v)}")
        it.setData(Qt.ItemDataRole.UserRole, list(v))
        self.addItem(it)
        self._renumber()
        return True

    def _renumber(self):
        for i in range(self.count()):
            it = self.item(i)
            it.setText(f"{i + 1:02d} {dx7.name(it.data(Qt.ItemDataRole.UserRole))}")

    def voices(self) -> list:
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]

    def remove_selected(self):
        for it in self.selectedItems():
            self.takeItem(self.row(it))
        self._renumber()

    def clear_all(self):
        self.clear()

    def dragEnterEvent(self, e):
        e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        src = e.source()
        if src is self:
            super().dropEvent(e)
            self._renumber()
            return
        if isinstance(src, QTreeWidget):
            for it in src.selectedItems():
                ref = it.data(0, Qt.ItemDataRole.UserRole)
                if ref is not None:
                    self.add(ref.voice)
            e.acceptProposedAction()


class CollectionsDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setWindowTitle("DX7-Sammlungen")
        self.setMinimumSize(640, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Freie DX7-Sammlungen auf GitHub. Sie landen in FM-1/DX7/Sammlungen. "
                             "Quelle und Lizenz stehen bei jeder Sammlung."))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Sammlung", "Lizenz", "Dateien", "Status"])
        self.tree.setRootIsDecorated(False)
        lay.addWidget(self.tree, 1)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setOpenExternalLinks(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.info)
        bb = QDialogButtonBox()
        self.b_dl = bb.addButton("Herunterladen", QDialogButtonBox.ButtonRole.ActionRole)
        self.b_dl.clicked.connect(self.download)
        bb.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        german_buttons(bb)
        lay.addWidget(bb)
        self.tree.currentItemChanged.connect(self._show)
        self.fill()

    @property
    def root(self) -> Path:
        return self.page.root / "Sammlungen"

    def fill(self):
        self.tree.clear()
        for c in dx7_sources.COLLECTIONS:
            it = QTreeWidgetItem([c.title, c.licence if c.clear else "unklar", str(len(c.files)),
                                  "geladen" if dx7_sources.installed(self.root, c) else ""])
            it.setData(0, Qt.ItemDataRole.UserRole, c)
            self.tree.addTopLevelItem(it)
        self.tree.resizeColumnToContents(0)
        self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _show(self, it, _=None):
        if it:
            c = it.data(0, Qt.ItemDataRole.UserRole)
            self.info.setText(f"{c.description}<br>Quelle: <a href='{c.url}'>{c.repo}</a> · Lizenz: <b>{c.licence}</b>")

    def download(self, _=False, collection=None, fetch=None):
        it = self.tree.currentItem()
        c = collection or (it.data(0, Qt.ItemDataRole.UserRole) if it else None)
        if not c:
            return
        if not c.clear and not confirm(self, "Lizenz unklar", f"{c.title}: {dx7_sources.UNCLEAR}.\n\nQuelle: {c.url}\n\n"
                                       "Trotzdem für den privaten Gebrauch herunterladen?", "Herunterladen"):
            return
        self.b_dl.setEnabled(False)
        self.info.setText(f"Lade {c.title} …")

        def done(d):
            self.b_dl.setEnabled(True)
            self.fill()
            self.info.setText(f"{c.title}: heruntergeladen ({d}) – liegt in FM-1/DX7/Sammlungen. "
                              "Ans FM-1 wurde nichts gesendet.")
            notify(f"DX7-Sammlung „{c.title}“ geladen ({d}).", "success")
            self.page.scan()

        def fail(m, tb):
            self.b_dl.setEnabled(True)
            self.info.setText(f"Herunterladen fehlgeschlagen: {dx7_sources.net_error_text(m)} Schon geladene Dateien "
                              "bleiben; „Herunterladen“ holt den Rest nach.")
        run_job(self, lambda w: dx7_sources.download(c, self.root, fetch=fetch, progress=lambda a, b: w.progress.emit(a, b)),
                on_done=done, on_fail=fail,
                on_progress=lambda a, b: self.info.setText(f"Lade {c.title} … {a} von {b} Dateien"))


def _make(ctx, window):
    return VoicesPage(ctx, window)


PAGE = PageSpec(key="voices", title="FM-Voices", group="klang", factory=_make,
                icons=("audio-x-generic", "audio-midi", "preferences-desktop-sound"), order=20,
                description="DX7-Voices sammeln, anhören, erzeugen und ans FM-1 senden",
                keywords=("dx7", "fm6", "syx", "sysex", "dexed", "voice", "bank", "algorithmus"))
