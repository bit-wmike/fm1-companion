# SPDX-License-Identifier: GPL-3.0-only
"""FM voices: manage DX7 banks, listen to voices (computer preview), send to the FM-1, build custom banks,
generate new voices (random, mutation, morph, variations)."""
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
from ...i18n import _, ngettext, pgettext

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", pgettext("note name", "B")]
GENERATED = _("Generated")           # name of the bank with generated voices (in memory only)


@dataclass
class VoiceRef:
    voice: list
    bank: str              # display name of the bank
    index: int             # position in the bank
    path: Path | None = None

    @property
    def name(self) -> str:
        return dx7.name(self.voice) or _("(no name)")


# ------------------------------------------------------------------ Graphics ---

class AlgoView(QWidget):
    """draws one of the 32 algorithms: carriers at the bottom, modulators above, feedback as a loop."""

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

    def paintEvent(self, _event):
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
        p.drawText(6, 14, _("Algorithm {alg} · feedback {fb}").format(alg=self.alg + 1, fb=self.fb) if self.fb
                   else _("Algorithm {alg}").format(alg=self.alg + 1))


class EnvView(QWidget):
    """the six operator envelopes (time roughly from the rates, level from the levels)."""

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

    def paintEvent(self, _event):
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
        p.drawText(6, 14, _("Envelopes (carriers bold)"))


# ------------------------------------------------------------------ Page ---

class VoicesPage(QWidget):
    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.banks: dict[str, list[VoiceRef]] = {}
        self.current: VoiceRef | None = None
        self.partner: VoiceRef | None = None
        self.undo = DeviceUndo()           # (slot, old preset) per device
        self.fs_error = ""
        self.player = None
        self.render_seq = 0
        self.last_render = None
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("FM Voices"), _("DX7 voices for Melodee's FM6 engine and every FM-1: collect "
                                                "banks, audition them on the computer, send them to the FM-1, build your own "
                                                "banks and create new sounds. Folder: FM-1/DX7 (watched).")))
        self.state = QLabel("")
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search all banks: name, bank, “alg 5”, tag …  (Ctrl+F)"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search.setFocus)
        tb = []
        for text, ic, fn, tip in ((_("Import …"), "document-import", self.import_files, _("copy .syx files into FM-1/DX7")),
                                  (_("Folder …"), "folder-open", self.import_folder, _("take over all .syx of a folder")),
                                  (_("Collections …"), "download", self.collections, _("download free DX7 collections from GitHub"))):
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
        # empty state (no banks): a starting point instead of empty tables
        self.stack = QStackedWidget()
        self.start = empty_state("audio-lines", _("No DX7 voices yet"),
                                 _("DX7 sounds for the FM6 engine: “Collections” downloads free banks from GitHub (e.g. the 128 "
                                   "factory voices), “Import” takes over your own .syx files into FM-1/DX7."),
                                 _("Load collections …"), self.collections)
        b_imp0 = button(_("Import your own .syx files …"), "document-import", slot=self.import_files)
        self.start.layout().addWidget(b_imp0, 0, Qt.AlignmentFlag.AlignHCenter)
        self.stack.addWidget(self.start)
        self.stack.addWidget(sp)
        lay.addWidget(self.stack, 1)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.bank_list = QListWidget()
        self.bank_list.setMaximumHeight(140)
        self.bank_list.currentRowChanged.connect(lambda _row: self.fill())
        ll.addWidget(QLabel(_("<b>Banks</b>")))
        ll.addWidget(self.bank_list)
        self.table = QTreeWidget()
        self.table.setHeaderLabels([_("Voice"), _("Bank"), _("No."), "Alg."])
        self.table.setRootIsDecorated(False)
        self.table.setSortingEnabled(True)
        self.table.setDragEnabled(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.currentItemChanged.connect(self._pick)
        self.table.itemDoubleClicked.connect(lambda *_args: self.preview())
        ll.addWidget(self.table, 1)
        self.empty = QLabel(_("No DX7 banks yet – here's how: “Collections …” downloads e.g. the 128 factory voices of the "
                              "FM-1, or “Import …” takes over your .syx files."))
        self.empty.setWordWrap(True)
        ll.addWidget(self.empty)
        sp.addWidget(left)

        # ---- Detail
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        right = QWidget()
        scroll.setWidget(right)
        rl = QVBoxLayout(right)
        self.title = QLabel(_("No voice chosen"))
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
        self.ops.setHorizontalHeaderLabels(["Op", _("Mode"), _("Frequency"), _("Det."), pgettext("DX7 operator", "Level"), _("Rates"), _("Levels"), _("Vel."),
                                            _("Role")])
        self.ops.verticalHeader().setVisible(False)
        self.ops.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ops.setMinimumHeight(200)
        rl.addWidget(self.ops)
        # Preview
        pv = QGroupBox(_("Listen (calculated on the computer – similar, not identical to the FM-1)"))
        pl_ = QVBoxLayout(pv)
        hk = []
        self.b_play = QPushButton(icon("media-playback-start"), _("Listen"))
        self.b_play.setToolTip(_("C4 for two seconds (Space)"))
        self.b_play.clicked.connect(lambda: self.preview())
        QShortcut(QKeySequence("Space"), self.table, activated=lambda: self.preview())
        hk.append(self.b_play)
        self.keys = []
        for n in range(60, 73):
            if NOTE_NAMES[n % 12].endswith("#"):
                continue
            b = QPushButton(f"{NOTE_NAMES[n % 12]}{n // 12 - 1}")
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(b.text()) + 22)
            b.setToolTip(_("Audition note {n}").format(n=n))
            b.clicked.connect(lambda _checked=False, n=n: self.preview(note=n))
            hk.append(b)
            self.keys.append(b)
        from ..audio import OutputButton, Waveform
        hk.append(OutputButton(self))
        pl_.addWidget(flow_row(*hk, hspacing=4))
        self.wave = Waveform()
        pl_.addWidget(self.wave)
        rl.addWidget(pv)
        # Send / export
        sd = QGroupBox(_("To the FM-1 and to files"))
        sl = QVBoxLayout(sd)
        self.b_send = QPushButton(icon("document-send"), _("To the FM-1 …"))
        self.b_send.setToolTip(_("Melodee: save to a user slot or live onto a track; other firmware: as DX7 SysEx"))
        self.b_send.clicked.connect(self.send)
        self.b_undo = QPushButton(icon("edit-undo"), _("Undo"))
        self.b_undo.clicked.connect(self.do_undo)
        self.b_undo.setEnabled(False)
        b_exp = QPushButton(icon("document-export"), _("As .syx …"))
        b_exp.setToolTip(_("save this voice as single-voice SysEx"))
        b_exp.clicked.connect(lambda: self.export_voice())
        b_add = QPushButton(icon("list-add"), _("To own bank"))
        b_add.clicked.connect(self.add_to_own)
        sl.addWidget(flow_row(self.b_send, self.b_undo, b_exp, b_add))
        rl.addWidget(sd)
        # Generators
        gb = QGroupBox(_("Create new sounds"))
        gl = QGridLayout(gb)
        b_rand = QPushButton(icon("roll"), _("Random voice"))
        b_rand.setToolTip(_("musically restricted randomness (audible carriers, sensible envelopes)"))
        b_rand.clicked.connect(self.gen_random)
        gl.addWidget(b_rand, 0, 0)
        self.amount = QSlider(Qt.Orientation.Horizontal)
        self.amount.setRange(5, 100)
        self.amount.setValue(30)
        self.amount.setToolTip(_("Strength of the mutation"))
        self.keep_alg = QCheckBox(_("Keep algorithm"))
        self.keep_alg.setChecked(True)
        b_mut = QPushButton(icon("tools-wizard"), _("Mutate"))
        b_mut.clicked.connect(self.gen_mutate)
        gl.addWidget(QLabel(_("Strength:")), 1, 0)
        gl.addWidget(self.amount, 1, 1)
        gl.addWidget(self.keep_alg, 1, 2)
        gl.addWidget(b_mut, 1, 3)
        b_partner = QPushButton(_("Remember as morph partner"))
        b_partner.clicked.connect(self.set_partner)
        self.morph = QSlider(Qt.Orientation.Horizontal)
        self.morph.setRange(0, 100)
        self.morph.setValue(50)
        b_morph = QPushButton(_("Morph"))
        b_morph.clicked.connect(self.gen_morph)
        self.partner_lab = QLabel(_("Partner: –"))
        gl.addWidget(b_partner, 2, 0)
        gl.addWidget(self.morph, 2, 1)
        gl.addWidget(self.partner_lab, 2, 2)
        gl.addWidget(b_morph, 2, 3)
        b_var = QPushButton(icon("view-list-icons"), _("Create variations"))
        b_var.setToolTip(_("8 variations of the voice – listen (double-click) and keep the good ones"))
        b_var.clicked.connect(self.gen_variations)
        gl.addWidget(b_var, 3, 0)
        self.children = QListWidget()
        self.children.setMaximumHeight(110)
        self.children.setFlow(QListWidget.Flow.LeftToRight)
        self.children.setWrapping(True)
        self.children.itemDoubleClicked.connect(self._child_play)
        self.children.currentRowChanged.connect(self._child_pick)
        gl.addWidget(self.children, 4, 0, 1, 3)
        b_keep = QPushButton(icon("dialog-ok"), _("Keep"))
        b_keep.setToolTip(_("take the chosen variation into your own bank"))
        b_keep.clicked.connect(self.keep_child)
        gl.addWidget(b_keep, 4, 3)
        rl.addWidget(gb)
        # Own bank
        ob = QGroupBox(_("Own bank (32 slots – drag voices here)"))
        ol = QVBoxLayout(ob)
        self.own = OwnBank(self)
        ol.addWidget(self.own)
        obs = []
        for text, fn in ((_("Remove"), self.own.remove_selected), (_("Clear"), self.own.clear_all),
                         (_("Save bank (.syx) …"), lambda: self.save_own())):   # (clicked passes False as target)
            b = QPushButton(text)
            b.clicked.connect(fn)
            obs.append(b)
        ol.addWidget(flow_row(*obs))
        rl.addWidget(ob)
        rl.addStretch(1)
        sp.addWidget(scroll)
        sp.setSizes([420, 700])

        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _path: QTimer.singleShot(400, self.scan))
        ctx.changed.connect(self._dev_changed)
        self.scan()

    def deactivate(self):
        """Leaving the page: stop the preview."""
        if self.player is not None:
            self.player.stop()

    def shutdown(self):
        self.deactivate()

    def _dev_changed(self):
        self.b_undo.setEnabled(bool(self.undo))
        self.b_undo.setToolTip("" if not self.undo or self.undo.matches(self.ctx) else self.undo.refusal())

    # ------------------------------------------------------------ Banks
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
            self.state.setText(_("{error} Listening and creating work anyway.").format(error=self.fs_error))
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
            label = f"{rel.parent.name + ' / ' if rel.parent.name and rel.parent.name != 'Sammlungen' else ''}{f.stem}"  # i18n: ignore (folder name)
            banks[label] = [VoiceRef(v, label, i, f) for i, v in enumerate(res.voices)]
        for k in (GENERATED,):
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
        self.bank_list.addItem(_("All banks ({n} voices)").format(n=sum(len(v) for v in banks.values())))
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
            vals = [str(op), _("fixed") if fixed else _("ratio"), freq, f"{v[dx7.at(op, 'DET')] - 7:+d}",
                    str(v[dx7.at(op, "OL")]),
                    " ".join(str(v[dx7.at(op, f"R{i}")]) for i in range(1, 5)),
                    " ".join(str(v[dx7.at(op, f"L{i}")]) for i in range(1, 5)),
                    str(v[dx7.at(op, "KVS")]), _("Carrier") if op in car else _("Modulator")]
            for c, t in enumerate(vals):
                self.ops.setItem(row, c, QTableWidgetItem(t))
        self.ops.resizeColumnsToContents()

    # ------------------------------------------------------------ Import
    def import_files(self, files=None):
        if not files:
            files, _filter = QFileDialog.getOpenFileNames(self, _("Import DX7 banks"), str(Path.home()),
                                                          _("DX7 SysEx (*.syx *.SYX);;All files (*)"))
        n, bad = 0, []
        target = self.root / "Importiert"  # i18n: ignore (folder name)
        if files:
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                show_error(self, _("Import"), fs_error_text(e, _("Folder FM-1/DX7 not writable")))
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
                bad.append(_("{name}: no DX7 voices").format(name=f.name))
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
            done = ngettext("{n} file taken over.", "{n} files taken over.", n).format(n=n)
            self.state.setText(_("{done} Notes: {notes}").format(done=done, notes=" · ".join(bad)) if bad else done)

    def import_folder(self, folder=None):
        if not folder:
            folder = QFileDialog.getExistingDirectory(self, _("Folder with .syx files"), str(Path.home()))
        if folder:
            self.import_files([str(p) for p in Path(folder).rglob("*") if p.suffix.lower() == ".syx"])

    def collections(self):
        d = CollectionsDialog(self)
        d.exec()
        self.scan()

    # ------------------------------------------------------------ Listen
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
            path = d / f"vorschau-{seq % 4}.wav"  # i18n: ignore (file name)
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
            self.state.setText(_("Preview calculated in {secs} s.").format(secs=f"{secs:.2f}"))
        run_job(self, work, on_done=done, on_fail=lambda m, tb: self.state.setText(_("Preview failed: {error}").format(error=m)))

    # ------------------------------------------------------------ Send
    def send(self):
        if not self.current:
            return
        v = self.current.voice
        ctx = self.ctx
        midi = getattr(ctx, "midi_connected", ctx.link is not None)
        if not midi:
            show_error(self, _("No device"), _("Please connect an FM-1 via USB."))
            return
        fw = ctx.firmware_key or ""
        opt_slot = _("Save to a user slot (FM6 preset) …")
        opt_live = _("Load live onto a track (not saved)")
        options = []
        if ctx.connected and fw == "melodee":
            options += [opt_slot, opt_live]
        options.append(_("Send as DX7 SysEx"))
        item, ok = QInputDialog.getItem(self, _("To the FM-1"), _("“{name}” – how?").format(name=self.current.name), options, 0, False)
        if not ok:
            return
        if item == opt_slot:
            self.send_to_slot(v)
        elif item == opt_live:
            tr, ok = QInputDialog.getInt(self, _("Track"), _("On which track (1–4)?"), 1, 1, 4)
            if ok:
                self._melodee_job(_("Load voice onto track"),
                                  lambda w, bank: bank.voice_to_track(tr - 1, bytes(dx7.pack(v))),
                                  lambda _res: self.state.setText(_("“{name}” now plays on track {track} "
                                                                    "(FM6). Nothing was saved.").format(name=self.current.name, track=tr)))
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
                # not a glitch but a property of the firmware: explain calmly
                self.state.setText(_("Not possible on this FM-1 – nothing was changed on the FM-1. {reason}").format(reason=m))
                show_error(self, _("Not possible on this FM-1"), _("{reason}\n\nNothing was changed on the FM-1.").format(reason=m))
                return
            self.state.setText(_("{title} failed: {error} Nothing was changed on the FM-1.").format(title=title, error=m))
            show_error(self, title, m, tb)
        self.state.setText(f"{title} …")
        self.ctx.job(self, title, work, dialog=False, on_done=done, on_fail=fail,
                     on_progress=lambda a, b: self.state.setText(_("{title} … {a} of {b}").format(title=title, a=a, b=b)))

    def send_to_slot(self, v, slot: int | None = None):
        def find(w, bank):
            return bank.list_all(progress=lambda a, b: w.progress.emit(a, b))

        tag0 = device_tag(self.ctx)

        def gone() -> bool:
            """Device changed since the slots were read? Then don't write – the occupancy is no longer accurate."""
            if same_device(tag0, device_tag(self.ctx)):
                return False
            self.state.setText(_("The FM-1 was changed while you were choosing the slot – nothing saved. "
                                 "Please “To the FM-1” once more."))
            return True

        def chosen(slots):
            self.state.setText("")
            if gone():
                return
            free = next((s["slot"] for s in slots if not s["used"]), 0)
            k = slot
            if k is None:
                k, ok = QInputDialog.getInt(self, _("User slot"), _("Into which slot (1–384)? Suggested: the first free one."),
                                            free + 1, 1, len(slots))
                if not ok:
                    return
                k -= 1
            s = slots[k]
            label = f"U{k + 1:03d}"
            what = (_("{slot} “{name}” ({engine}) will be overwritten.").format(
                        slot=label, name=s['name'], engine=pl.engine_display_name(s['engine']))
                    if s["used"] else _("{slot} is empty.").format(slot=label))
            if not confirm(self, _("Save as FM6 preset"),
                           _("Save “{name}” as FM6 preset in {slot}?\n\n{what}\n\nThe old content is "
                             "read first (undo possible). The FM-1 stops playback for this.").format(
                               name=dx7.name(v), slot=label, what=what),
                           _("Save"), danger=bool(s["used"])) or gone():
                return
            p = pl.preset_from_voice(v)
            tag = device_tag(self.ctx)

            def write(w, bank):
                return bank.write_with_undo(k, p)

            def done(old):
                self.undo.push(self.ctx, (k, old), f"U{k + 1:03d}", tag)
                self._dev_changed()
                self.state.setText(_("“{name}” is now in {slot} (FM6). “Undo” restores the old content.").format(
                    name=dx7.name(v), slot=f"U{k + 1:03d}"))
            self._melodee_job(_("Save FM6 preset"), write, done)
        self._melodee_job(_("Read user slots"), find, chosen)

    def do_undo(self):
        if not self.undo or not self.undo.check(self.ctx, self):
            return
        k, old = self.undo.peek()
        question = (_("Set {slot} back to “{name}”?").format(slot=f"U{k + 1:03d}", name=old.name) if old.used
                    else _("Set {slot} back to empty?").format(slot=f"U{k + 1:03d}"))
        if not confirm(self, _("Undo"), question, _("Revert")):
            return

        def done(_res):
            self.undo.pop()
            self._dev_changed()
            self.state.setText(_("{slot} reset.").format(slot=f"U{k + 1:03d}"))
        self._melodee_job(_("Undo"), lambda w, bank: bank.restore(k, old), done)

    def send_sysex(self, v):
        ident = getattr(self.ctx, "identity", "") or (self.ctx.profile.identity if self.ctx.profile else "")
        plan = dx7.send_plan(self.ctx.firmware_key, ident)
        ch, ok = QInputDialog.getInt(self, _("MIDI channel"), _("On which MIDI channel (1–16)?"), 1, 1, 16)
        if not ok:
            return
        if plan == "single":
            text = _("Send a single-voice dump. Depending on the firmware it replaces the sound of the matching FM track "
                     "(Melodee: the FM6 track; M-VAVE firmware: the chosen preset – hold SAVE on the device to keep it).")
            msgs = [dx7.single_syx(v, ch - 1)]
        else:
            text = _("This firmware is not known for sure: the voice goes out as 155 single DX7 parameter changes. "
                     "That only changes the current sound and overwrites no saved preset.")
            msgs = dx7.param_changes(v, ch - 1)
        if not confirm(self, _("Send DX7 SysEx"), _("“{name}” on channel {channel}: {text}").format(name=dx7.name(v), channel=ch, text=text),
                       _("Send")):
            return
        link = self.ctx.link
        sent = sum(1 for m in msgs if link and link.send_midi(list(m)))
        self.state.setText(ngettext("{n} SysEx message sent.", "{n} SysEx messages sent.", sent).format(n=sent) if sent == len(msgs)
                           else _("Sending not possible – the device is busy right now."))

    # ------------------------------------------------------------ Export / own bank
    def export_voice(self, target=None):
        if not self.current:
            return
        if target is None:
            target, _filter = QFileDialog.getSaveFileName(self, _("Save voice"), str(Path.home() / (pl.safe_file(self.current.name) + ".syx")),
                                                          _("DX7 SysEx (*.syx)"))
        if target:
            try:
                Path(target).write_bytes(dx7.single_syx(self.current.voice))
            except OSError as e:
                show_error(self, _("Save voice"), fs_error_text(e, _("Not saved")))
                return
            self.state.setText(_("File saved: {name}").format(name=Path(target).name))

    def add_to_own(self):
        refs = [it.data(0, Qt.ItemDataRole.UserRole) for it in self.table.selectedItems()] or ([self.current] if self.current else [])
        for r in refs:
            self.own.add(r.voice)
        if refs:
            self.state.setText(ngettext("{n} voice added to your own bank – “Save bank” creates the .syx.",
                                        "{n} voices added to your own bank – “Save bank” creates the .syx.", len(refs)).format(n=len(refs)))

    def save_own(self, target=None):
        if not self.own.voices():
            show_error(self, _("Own bank"), _("Your own bank is empty – drag voices from the list here."))
            return
        if target is None:
            name, ok = QInputDialog.getText(self, _("Save bank"), _("Name of the bank:"), text=_("My bank"))
            if not ok or not name.strip():
                return
            target = self.root / "Eigene Bänke" / (pl.safe_file(name) + ".syx")  # i18n: ignore (folder name)
        target = Path(target)
        vs = self.own.voices()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(dx7.bank_syx(vs))
        except OSError as e:
            show_error(self, _("Save bank"), fs_error_text(e, _("Not saved")))
            return
        self.state.setText(_("Bank saved: {name} ({n} voices, rest INIT VOICE).").format(name=target.name, n=len(vs)))
        self.scan()

    # ------------------------------------------------------------ Generators
    def _generated(self, v, label=GENERATED) -> VoiceRef:
        lst = self.banks.setdefault(GENERATED, [])
        ref = VoiceRef(v, GENERATED, len(lst))
        lst.append(ref)
        if not self.bank_list.findItems(GENERATED, Qt.MatchFlag.MatchExactly):
            self.bank_list.addItem(GENERATED)
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
            self.partner_lab.setText(_("Partner: {name}").format(name=self.current.name))

    def gen_morph(self):
        if not (self.current and self.partner):
            show_error(self, _("Morph"), _("First remember a voice as morph partner, then choose a second one."))
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
            self.state.setText(_("“{name}” kept (bank “{bank}” and own bank).").format(name=dx7.name(v), bank=GENERATED))


class OwnBank(QListWidget):
    """up to 32 voices; drag & drop from the voice list, reorder by dragging."""

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
            self.page.state.setText(_("Your own bank is full (32 voices)."))
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
        self.setWindowTitle(_("DX7 collections"))
        self.setMinimumSize(640, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("Free DX7 collections on GitHub. They go to FM-1/DX7/Sammlungen. "
                               "Source and licence are given for each collection.")))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Collection"), _("Licence"), _("Files"), pgettext("column", "Status")])
        self.tree.setRootIsDecorated(False)
        lay.addWidget(self.tree, 1)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setOpenExternalLinks(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.info)
        bb = QDialogButtonBox()
        self.b_dl = bb.addButton(_("Download"), QDialogButtonBox.ButtonRole.ActionRole)
        self.b_dl.clicked.connect(self.download)
        bb.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        german_buttons(bb)
        lay.addWidget(bb)
        self.tree.currentItemChanged.connect(self._show)
        self.fill()

    @property
    def root(self) -> Path:
        return self.page.root / "Sammlungen"  # i18n: ignore (folder name)

    def fill(self):
        self.tree.clear()
        for c in dx7_sources.COLLECTIONS:
            it = QTreeWidgetItem([c.title, c.licence if c.clear else _("unclear"), str(len(c.files)),
                                  _("downloaded") if dx7_sources.installed(self.root, c) else ""])
            it.setData(0, Qt.ItemDataRole.UserRole, c)
            self.tree.addTopLevelItem(it)
        self.tree.resizeColumnToContents(0)
        self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _show(self, it, _prev=None):
        if it:
            c = it.data(0, Qt.ItemDataRole.UserRole)
            self.info.setText(_("{description}<br>Source: <a href='{url}'>{repo}</a> · Licence: <b>{licence}</b>").format(
                description=c.description, url=c.url, repo=c.repo, licence=c.licence))

    def download(self, _checked=False, collection=None, fetch=None):
        it = self.tree.currentItem()
        c = collection or (it.data(0, Qt.ItemDataRole.UserRole) if it else None)
        if not c:
            return
        if not c.clear and not confirm(self, _("Licence unclear"), _("{title}: {unclear}.\n\nSource: {url}\n\n"
                                                                   "Download anyway for private use?").format(
                                           title=c.title, unclear=dx7_sources.UNCLEAR, url=c.url), _("Download")):
            return
        self.b_dl.setEnabled(False)
        self.info.setText(_("Loading {title} …").format(title=c.title))

        def done(d):
            self.b_dl.setEnabled(True)
            self.fill()
            self.info.setText(_("{title}: downloaded ({folder}) – it is in FM-1/DX7/Sammlungen. "
                                "Nothing was sent to the FM-1.").format(title=c.title, folder=d))
            notify(_("DX7 collection “{title}” downloaded ({folder}).").format(title=c.title, folder=d), "success")
            self.page.scan()

        def fail(m, tb):
            self.b_dl.setEnabled(True)
            self.info.setText(_("Download failed: {error} Files already downloaded stay; "
                                "“Download” fetches the rest.").format(error=dx7_sources.net_error_text(m)))
        run_job(self, lambda w: dx7_sources.download(c, self.root, fetch=fetch, progress=lambda a, b: w.progress.emit(a, b)),
                on_done=done, on_fail=fail,
                on_progress=lambda a, b: self.info.setText(_("Loading {title} … {a} of {b} files").format(title=c.title, a=a, b=b)))


def _make(ctx, window):
    return VoicesPage(ctx, window)


PAGE = PageSpec(key="voices", title=_("FM Voices"), group="klang", factory=_make,
                icons=("audio-x-generic", "audio-midi", "preferences-desktop-sound"), order=20,
                description=_("collect, audition, generate DX7 voices and send them to the FM-1"),
                keywords=("dx7", "fm6", "syx", "sysex", "dexed", "voice", "bank", "algorithmus", "algorithm", "fm voices"))
