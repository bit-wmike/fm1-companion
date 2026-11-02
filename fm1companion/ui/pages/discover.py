# SPDX-License-Identifier: GPL-3.0-only
"""Firmware+ (früher „Entdecken“): alle bekannten FM-1-Firmwares wie in einem App-Store – suchen, filtern,
ansehen, im Simulator ausprobieren, installieren. Dazu „Link einfügen …", Neuigkeiten und Werkzeuge & Wissen.
Das Hauptfenster hängt die frühere Seite „Firmware“ (ui/firmware_page.py) als Reiter „Installieren & Versionen“
ein (main_window.FLAT_COMPOSITES) – Installieren, Versionen wechseln und der Rückweg zum Original liegen dort.

Daten: catalog.py (mitgelieferter Schnappschuss → Live-Stand alle 24 h → GitHub-Cache).
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QKeySequence,
                         QPainter, QPainterPath, QPalette, QPixmap, QShortcut)
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QListView, QMenu, QMessageBox, QPushButton, QScrollArea, QStackedWidget,
                             QStyle, QStyledItemDelegate, QTabWidget, QTextBrowser, QToolButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import catalog as catmod
from ... import config, firmware
from ..common import notify, fmt_size, heading, icon, run_job
from ..responsive import reflow
from ..linkimport_dialog import LinkImportDialog, OwnFirmwaresDialog, _looks_like_link
from . import PageSpec

CAT_COLORS = {"Original": "#8e44ad", "Synth": "#2a7ab8", "Groovebox": "#e67e22", "Sampler": "#16a085",
              "Akkorde": "#c0398b", "Generativ": "#27ae60", "Werkzeug": "#7f8c8d", "Spiele & Spaß": "#d35400"}
SORTS = [("az", "A–Z"), ("updated", "zuletzt aktualisiert"), ("stars", "Beliebtheit (Sterne)"),
         ("installable", "direkt installierbar zuerst")]
CHIPS = [("installable", "Direkt installierbar", "Paket mit Prüfsumme im Katalog – die App kann es aufspielen"),
         ("emulator", "Mit Simulator", "im Browser ausprobieren, ohne etwas aufzuspielen"),
         ("editor", "Mit Editor", "hat einen Online-Editor"),
         ("backup", "Backup-fähig", "Felucca-Protokoll: Backups, Projekte und Editor-Brücke in der App"),
         ("wish", "Gemerkt", "deine Merkliste")]
DEFAULTS = {"wishlist": [], "github_token": "", "last_visit": 0.0, "thumbnails": True, "sort": "az",
            "auto_refresh": True}


def net_allowed() -> bool:
    return not os.environ.get("FM1C_NO_NET")


def settings() -> dict:
    return config.module("catalog", DEFAULTS)


def entry_date(e: catmod.Entry, gh: dict | None) -> str:
    if gh and gh.get("releases"):
        return gh["releases"][0]["date"]
    return (gh or {}).get("pushed_at") or e.pushed_at or ""


# ================================================================ Modell ===

class EntryModel(QAbstractListModel):
    thumb_wanted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[catmod.Entry] = []
        self.thumbs: dict[str, QPixmap] = {}
        self.wish: set[str] = set()

    def set_items(self, items):
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        e = self.items[idx.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return e.name
        if role == Qt.ItemDataRole.UserRole:
            return e
        if role == Qt.ItemDataRole.ToolTipRole:
            own = e.section == "own"
            return f"<b>{e.name}</b><br>" + ("" if own else f"<i>{EN_LABEL}:</i><br>") + e.description[:300]
        return None

    def thumb(self, e: catmod.Entry) -> QPixmap | None:
        if e.key in self.thumbs:
            return self.thumbs[e.key]
        if e.screenshot:
            self.thumb_wanted.emit(e.key)
        return None

    def set_thumb(self, key: str, pm: QPixmap):
        self.thumbs[key] = pm
        for i, e in enumerate(self.items):
            if e.key == key:
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)


def badges(e: catmod.Entry) -> list[tuple[str, str]]:
    out = [(c, CAT_COLORS.get(c, "#888")) for c in e.categories[:1]]
    if e.lineage:
        out.append((e.lineage[-1], "#5d6d7e"))
    if e.installable:
        out.append(("installierbar", "#2e9d4f"))
    if e.beta:
        out.append(("Beta", "#c77700"))
    if e.section == "closed":
        out.append(("geschlossen", "#7f8c8d"))
    if "kostenpflichtig" in e.flags:
        out.append(("kostenpflichtig", "#b03a2e"))
    return out


def initials_pixmap(e: catmod.Entry, size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(CAT_COLORS.get((e.categories or ["Synth"])[0], "#2a7ab8"))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.18, size * 0.18)
    p.fillPath(path, col.darker(115))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * 0.38))
    p.setFont(f)
    p.setPen(QColor("white"))
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in e.name).split() if w]
    txt = "".join(w[0] for w in words[:2]).upper() or "?"
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, txt[:2])
    p.end()
    return pm


def draw_badges(p: QPainter, x: int, y: int, maxx: int, items, small: QFont) -> int:
    fm = QFontMetrics(small)
    p.setFont(small)
    for text, col in items:
        w = fm.horizontalAdvance(text) + 12
        if x + w > maxx:
            break
        c = QColor(col)
        bg = QColor(c)
        bg.setAlpha(40)
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, fm.height() + 4), 8, 8)
        p.fillPath(path, bg)
        p.setPen(c.lighter(130) if QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
                 else c.darker(120))
        p.drawText(QRect(x, y, w, fm.height() + 4), Qt.AlignmentFlag.AlignCenter, text)
        x += w + 5
    return x


EN_LABEL = "Beschreibung des Autors (englisch)"


class CardDelegate(QStyledItemDelegate):
    W, H, T = 300, 150, 84                      # W: Mindestbreite; die tatsächliche Breite füllt die Zeile

    def __init__(self, parent=None):
        super().__init__(parent)
        self.width = self.W

    def sizeHint(self, option, index):
        return QSize(self.width, self.H)

    def paint(self, p: QPainter, option, index):
        e: catmod.Entry = index.data(Qt.ItemDataRole.UserRole)
        model: EntryModel = index.model()
        pal = option.palette
        r = option.rect.adjusted(5, 5, -5, -5)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        base = pal.color(QPalette.ColorRole.Base)
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 10, 10)
        p.fillPath(path, base.lighter(108) if hover and base.lightness() < 128 else (base.darker(103) if hover else base))
        pen = pal.color(QPalette.ColorRole.Highlight) if sel else pal.color(QPalette.ColorRole.Mid)
        p.setPen(pen)
        p.drawPath(path)
        t = self.T
        tr = QRect(r.left() + 10, r.top() + 10, t, t)
        pm = model.thumb(e)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(tr), 8, 8)
        if pm is not None and not pm.isNull():
            p.save()
            p.setClipPath(clip)
            sc = pm.scaled(t, t, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(tr.left() - (sc.width() - t) // 2, tr.top() - (sc.height() - t) // 2, sc)
            p.restore()
        else:
            p.drawPixmap(tr, initials_pixmap(e, t))
        x = tr.right() + 12
        maxx = r.right() - 10
        f = QFont(option.font)
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.12)
        p.setFont(f)
        p.setPen(pal.color(QPalette.ColorRole.Text))
        fm = QFontMetrics(f)
        star = "  ★" if e.key in model.wish else ""
        p.drawText(QRect(x, r.top() + 8, maxx - x, fm.height()), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(e.name + star, Qt.TextElideMode.ElideRight, maxx - x))
        y = r.top() + 8 + fm.height()
        small = QFont(option.font)
        small.setPointSizeF(small.pointSizeF() * 0.86)
        sm = QFontMetrics(small)
        p.setFont(small)
        p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
        by = ("von " + e.author) if e.author else ""
        if e.stars:
            by += f"   ★ {e.stars}"
        p.drawText(QRect(x, y, maxx - x, sm.height()), Qt.AlignmentFlag.AlignLeft,
                   sm.elidedText(by, Qt.TextElideMode.ElideRight, maxx - x))
        y += sm.height() + 3
        if e.section != "own" and e.description:
            ital = QFont(small)
            ital.setItalic(True)
            p.setFont(ital)
            p.drawText(QRect(x, y, maxx - x, sm.height()), Qt.AlignmentFlag.AlignLeft,
                       QFontMetrics(ital).elidedText(EN_LABEL + ":", Qt.TextElideMode.ElideRight, maxx - x))
            p.setFont(small)
            y += sm.height() + 1
        p.setPen(pal.color(QPalette.ColorRole.Text))
        desc_rect = QRect(x, y, maxx - x, sm.height() * 2 + 2)
        lines = _two_lines(e.description, sm, maxx - x)
        p.drawText(desc_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, lines)
        draw_badges(p, r.left() + 10, r.bottom() - sm.height() - 10, maxx, badges(e), small)
        p.restore()


def _two_lines(text: str, fm: QFontMetrics, width: int) -> str:
    words, lines, cur, rest = text.split(), [], "", 0
    for i, w in enumerate(words):
        t = (cur + " " + w).strip()
        if fm.horizontalAdvance(t) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
            if len(lines) == 2:
                rest = i                        # ab hier passt nichts mehr: Rest nur noch gekürzt anhängen
                break
    if len(lines) < 2:
        lines.append(cur)
        return "\n".join(x for x in lines if x)
    return lines[0] + "\n" + fm.elidedText(lines[1] + " " + " ".join(words[rest:]), Qt.TextElideMode.ElideRight,
                                           width)


# ============================================================== Detail ===

def _btn(text, icons, slot, tip="", primary=False) -> QPushButton:
    b = QPushButton(icon(*icons), text)
    b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    if primary:
        b.setDefault(True)
        f = b.font()
        f.setBold(True)
        b.setFont(f)
    return b


class DetailView(QWidget):
    back = pyqtSignal()

    def __init__(self, page: "DiscoverPage"):
        super().__init__()
        self.page = page
        self.entry: catmod.Entry | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        b = QPushButton(icon("go-previous"), "Alle Firmwares")
        b.setFlat(True)
        b.clicked.connect(self.back)
        top.addWidget(b)
        top.addStretch(1)
        outer.addLayout(top)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll, 1)
        sc = QShortcut(QKeySequence("Esc"), self)
        sc.activated.connect(self.back)
        sc2 = QShortcut(QKeySequence("Backspace"), self)
        sc2.activated.connect(self.back)

    def show_entry(self, e: catmod.Entry):
        self.entry = e
        self.labels = {}
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setSpacing(10)
        # ---- Kopf
        head = QHBoxLayout()
        self.pic = QLabel()
        self.pic.setFixedSize(160, 160)
        self.pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = self.page.model.thumbs.get(e.key)
        self.set_pic(pm if pm is not None else None)
        if pm is None and e.screenshot:
            self.page.want_thumb(e.key)
        head.addWidget(self.pic, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        t = QLabel(e.name)
        f = t.font()
        f.setPointSizeF(f.pointSizeF() * 1.8)
        f.setBold(True)
        t.setFont(f)
        t.setWordWrap(True)
        info.addWidget(t)
        sub = [f"von <b>{e.author}</b>" if e.author else "", e.license and f"Lizenz: {e.license}",
               " › ".join(e.lineage) and "Basis: " + " › ".join(e.lineage)]
        if e.stars:
            sub.append(f"★ {e.stars} auf GitHub")
        s = QLabel(" · ".join(x for x in sub if x))
        s.setWordWrap(True)
        info.addWidget(s)
        bl = QLabel(" ".join(f"<span style='background:rgba({QColor(c).red()},{QColor(c).green()},"
                             f"{QColor(c).blue()},0.18);color:{c}'>&nbsp;{t}&nbsp;</span>" for t, c in badges(e)))
        bl.setTextFormat(Qt.TextFormat.RichText)
        info.addWidget(bl)
        acts = QHBoxLayout()
        self.b_install = _btn("Installieren …", ("system-software-update", "go-down"), self.page.install,
                              "auf das aktive FM-1 aufspielen (mit Rückfrage und Backup)", primary=True)
        self.b_install.setEnabled(e.installable or e.builtin)
        if not (e.installable or e.builtin):
            self.b_install.setToolTip("Kein geprüftes Paket im Katalog – siehe Webseite/Installer des Autors")
        acts.addWidget(self.b_install)
        if e.emulator_url:
            acts.addWidget(_btn("Im Simulator ausprobieren", ("media-playback-start",), self.page.emulator,
                                "läuft im Rechner – nichts wird aufs FM-1 geschrieben"))
        if e.editor_url:
            acts.addWidget(_btn("Online-Editor öffnen", ("document-edit", "audio-midi"), self.page.editor))
        self.b_add = _btn("Zu meinen Firmwares", ("list-add",), self.page.add_own,
                          "erscheint dann auch im Reiter „Installieren & Versionen“")
        acts.addWidget(self.b_add)
        self.b_wish = QPushButton(icon("bookmark-new", "starred", "emblem-favorite"), "Merken")
        self.b_wish.setCheckable(True)
        self.b_wish.setChecked(e.key in self.page.model.wish)
        self.b_wish.toggled.connect(self.page.toggle_wish)
        acts.addWidget(self.b_wish)
        more = QToolButton()
        more.setText("Links")
        more.setIcon(icon("internet-web-browser", "applications-internet"))
        more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(more)
        for text, url in self.link_list(e):
            a = m.addAction(text)
            a.triggered.connect(lambda _c=False, u=url: QDesktopServices.openUrl(QUrl(u)))
        more.setMenu(m)
        acts.addWidget(more)
        acts.addStretch(1)
        info.addLayout(acts)
        reflow(acts)                                     # Knöpfe brechen bei schmalem Fenster um
        info.addStretch(1)
        head.addLayout(info, 1)
        lay.addLayout(head)

        # ---- Warnungen
        warns = e.warnings()
        if warns:
            w = QLabel("<b>Gut zu wissen</b><br>" + "<br>".join(f"⚠ {x}" for x in warns))
            w.setWordWrap(True)
            w.setTextFormat(Qt.TextFormat.RichText)
            w.setStyleSheet("background:rgba(230,126,34,0.13);border:1px solid rgba(230,126,34,0.5);"
                            "border-radius:8px;padding:8px;")
            lay.addWidget(w)
            self.labels["warnings"] = w
        # ---- Beschreibung
        d = QLabel(f"{e.description}" + ("" if e.section == "own" else
                                         f"<br><span style='color:gray'>{EN_LABEL}, aus awesome-fm-1 – "
                                         "bewusst nicht übersetzt</span>"))
        d.setWordWrap(True)
        d.setTextFormat(Qt.TextFormat.RichText)
        d.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(_section("Beschreibung", d))
        # ---- Matrix
        lay.addWidget(_section("Was die App damit kann", self.matrix(e)))
        # ---- Versionen
        self.versions = QTreeWidget()
        self.versions.setHeaderLabels(["Version", "Datum", "Größe", "Hinweise"])
        self.versions.setRootIsDecorated(False)
        self.versions.setMinimumHeight(140)
        self.fill_versions(e, catmod.cached_enrichment(e.repo) if e.repo else None)
        vb = QWidget()
        vl = QVBoxLayout(vb)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.versions)
        self.gh_state = QLabel()
        self.gh_state.setWordWrap(True)
        vl.addWidget(self.gh_state)
        lay.addWidget(_section("Versionen", vb))
        # ---- README
        self.readme = QTextBrowser()
        self.readme.setOpenExternalLinks(True)
        self.readme.setMinimumHeight(260)
        self.readme.setVisible(False)
        rb = QWidget()
        rl = QVBoxLayout(rb)
        rl.setContentsMargins(0, 0, 0, 0)
        self.b_readme = QPushButton(icon("document-preview", "text-x-generic"), "README des Projekts laden")
        self.b_readme.setEnabled(bool(e.repo))
        self.b_readme.clicked.connect(self.load_readme)
        rl.addWidget(self.b_readme, 0, Qt.AlignmentFlag.AlignLeft)
        rl.addWidget(self.readme)
        lay.addWidget(_section("Vom Autor", rb))
        lay.addStretch(1)
        self.scroll.setWidget(body)
        self.scroll.verticalScrollBar().setValue(0)
        if e.repo and net_allowed():
            self.page.load_github(e)

    def set_pic(self, pm):
        if pm is None or pm.isNull():
            pm = initials_pixmap(self.entry, 160)
        self.pic.setPixmap(pm.scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))

    @staticmethod
    def link_list(e: catmod.Entry) -> list[tuple[str, str]]:
        names = {"installer": "Web-Installer des Autors", "emulator": "Simulator im Browser", "editor": "Online-Editor",
                 "manual": "Handbuch", "site": "Webseite", "source": "Quellcode", "downloads": "Downloads (M-VAVE)",
                 "release_notes": "Versionshinweise (M-VAVE)", "midi": "MIDI-Referenz (M-VAVE)"}
        out = []
        if e.repo:
            out.append(("Auf GitHub ansehen", e.repo_url))
        for k, u in e.links.items():
            if not k.startswith("firmware_"):
                out.append((names.get(k, k), u))
        out.append(("Auf thefirmwarehub.com ansehen", f"{catmod.HUB_URL}#{e.hub_anchor}"))
        out.append(("Im FM1-Firmware-Verzeichnis ansehen", f"{catmod.DIRECTORY_URL}#{e.hub_anchor}"))
        return out

    def matrix(self, e: catmod.Entry) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        if e.installable or e.builtin:
            inst = ("✓", "Installieren über das Standard-Update-Protokoll von M-VAVE"
                    + (" – Prüfsumme wird vor dem Aufspielen geprüft" if e.installable else ""))
        elif "kostenpflichtig" in e.flags or e.section == "closed":
            inst = ("–", "Installieren nur über die Seite des Herstellers")
        else:
            inst = ("–", "Kein geprüftes Paket im Katalog; per „Link einfügen“ eine .fwsc-Adresse eintragen")
        if e.protocol == "felucca":
            bk = ("✓", f"Backups, Projekte und Editor-Brücke (Felucca-Protokoll; {e.protocol_evidence})")
        else:
            bk = ("?", "Backups/Projekte: unbekannt – wird beim Verbinden geprüft")
        rows = [("Installieren", *inst), ("Backup & Projekte", *bk),
                ("Editor", "✓" if e.editor_url or e.protocol == "felucca" else "✗",
                 "Online-Editor in der App" if e.editor_url else ("Editor aus dem Repo" if e.protocol == "felucca"
                                                                  else "kein Editor bekannt")),
                ("Emulator", "✓" if e.emulator_url else "✗",
                 "im Rechner ausprobieren" if e.emulator_url else "kein Browser-Emulator bekannt")]
        colors = {"✓": "#2e9d4f", "✗": "#888", "?": "#c77700", "–": "#888"}
        for i, (name, mark, text) in enumerate(rows):
            m = QLabel(f"<b style='color:{colors[mark]};font-size:14pt'>{mark}</b>")
            g.addWidget(m, i, 0)
            g.addWidget(QLabel(f"<b>{name}</b>"), i, 1)
            lt = QLabel(text)
            lt.setWordWrap(True)
            g.addWidget(lt, i, 2)
            self.labels[f"m_{name}"] = mark
        g.setColumnStretch(2, 1)
        return w

    def fill_versions(self, e, gh: dict | None):
        self.versions.clear()
        seen = set()
        for r in (gh or {}).get("releases", []):
            fw = [a for a in r["assets"] if a["name"].lower().endswith(".fwsc")]
            size = fmt_size(fw[0]["size"]) if fw else ""
            it = QTreeWidgetItem([r["tag"], r["date"], size, ("Vorabversion · " if r["prerelease"] else "")
                                  + ("Paket: " + fw[0]["name"] if fw else "kein .fwsc-Paket")])
            self.versions.addTopLevelItem(it)
            seen.add(r["tag"])
        for p in e.packages:
            if p.version in seen:
                continue
            seen.add(p.version)
            note = "im Mooring-Katalog, Prüfsumme bekannt" if p.downloadable else "bekanntes Paket (nur Erkennung)"
            short = {"beta": "Beta", "powerLossNotResumable": "Abbruch nicht fortsetzbar",
                     "projectsNotBackwardCompatible": "Projekte nicht abwärtskompatibel", "needsMiniLab3": "braucht MiniLab 3"}
            if p.notes:
                note += " · " + ", ".join(short.get(n, n) for n in p.notes)
            self.versions.addTopLevelItem(QTreeWidgetItem([p.version or p.label, "", fmt_size(p.raw_size), note]))
        if not self.versions.topLevelItemCount():
            self.versions.addTopLevelItem(QTreeWidgetItem(["keine Versionen bekannt"]))
        for c in range(4):
            self.versions.resizeColumnToContents(c)

    def github_loaded(self, key, info: dict | None, msg: str):
        if not self.entry or self.entry.key != key:
            return
        if info:
            self.fill_versions(self.entry, info)
        self.gh_state.setText(msg or ("Stand von GitHub" if info else ""))

    def load_readme(self):
        e = self.entry
        self.b_readme.setEnabled(False)
        self.b_readme.setText("lade …")

        def done(txt, key=e.key):
            if self.entry and self.entry.key == key:
                self.readme.setMarkdown(txt)
                self.readme.setVisible(True)
                self.b_readme.setVisible(False)

        def fail(msg, tb, key=e.key):
            if self.entry and self.entry.key == key:
                from ...dx7_sources import net_error_text
                self.b_readme.setText("README nicht geladen – erneut versuchen")
                self.b_readme.setToolTip(net_error_text(msg))
                self.b_readme.setEnabled(True)
        run_job(self, lambda w: catmod.repo_readme(e.repo), on_done=done, on_fail=fail)


def _section(title: str, w: QWidget) -> QWidget:
    box = QFrame()
    box.setFrameShape(QFrame.Shape.StyledPanel)
    l = QVBoxLayout(box)
    t = QLabel(title)
    f = t.font()
    f.setBold(True)
    f.setPointSizeF(f.pointSizeF() * 1.1)
    t.setFont(f)
    l.addWidget(t)
    l.addWidget(w)
    return box


# ================================================================ Seite ===

class DiscoverPage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, ctx, window=None):
        super().__init__()
        self.ctx, self.window = ctx, window
        self.cat = catmod.load()
        self.gh: dict[str, dict] = {}
        self._thumb_queue: list[str] = []
        self._thumb_busy = 0
        self._thumb_failed: set[str] = set()
        self._refreshing = False
        st = settings()
        self.last_visit = float(st.get("last_visit") or 0)

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.head = heading("Firmware+", "")
        top.addWidget(self.head, 1)
        self.b_link = QPushButton(icon("edit-paste", "insert-link"), "Link einfügen …")
        self.b_link.setToolTip("GitHub-Link, .fwsc-Paket, Installer- oder Simulator-Seite einfügen (Strg+V)")
        f = self.b_link.font()
        f.setBold(True)
        self.b_link.setFont(f)
        self.b_link.clicked.connect(lambda: self.link_import(""))
        self.b_refresh = QPushButton(icon("view-refresh"), "Aktualisieren")
        self.b_refresh.setToolTip("Katalog neu laden (awesome-fm-1 und Mooring)")
        self.b_refresh.clicked.connect(lambda: self.refresh(force=True))
        self.b_menu = QToolButton()
        self.b_menu.setIcon(icon("application-menu", "configure", "open-menu"))
        self.b_menu.setToolTip("Mehr")
        if self.b_menu.icon().isNull():
            self.b_menu.setText("Mehr")
        self.b_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(self.b_menu)
        m.addAction(icon("folder"), "Meine Firmwares …", self.own_dialog)
        m.addAction(icon("dialog-password", "security-high"), "GitHub-Token …", self.token_dialog)
        m.addSeparator()
        # automatische Netzzugriffe dieser Seite – beide abschaltbar (Datenschutz, siehe README)
        self.a_auto = m.addAction("Katalog beim Öffnen automatisch aktualisieren")
        self.a_auto.setCheckable(True)
        self.a_auto.setChecked(bool(settings().get("auto_refresh", True)))
        self.a_auto.toggled.connect(lambda on: self._set_pref("auto_refresh", on))
        self.a_thumbs = m.addAction("Vorschaubilder aus dem Internet laden")
        self.a_thumbs.setCheckable(True)
        self.a_thumbs.setChecked(bool(settings().get("thumbnails", True)))
        self.a_thumbs.toggled.connect(lambda on: self._set_pref("thumbnails", on))
        m.addSeparator()
        m.addAction("awesome-fm-1 auf GitHub", lambda: QDesktopServices.openUrl(QUrl(catmod.AWESOME_URL)))
        m.addAction("FM-1 Firmware Hub", lambda: QDesktopServices.openUrl(QUrl(catmod.HUB_URL)))
        m.addAction("FM1-Firmware-Verzeichnis", lambda: QDesktopServices.openUrl(QUrl(catmod.DIRECTORY_URL)))
        self.b_menu.setMenu(m)
        for b in (self.b_link, self.b_refresh, self.b_menu):
            top.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        # ---- Store
        store = QWidget()
        sl = QVBoxLayout(store)
        sl.setContentsMargins(0, 6, 0, 0)
        self.stack = QStackedWidget()
        grid_page = QWidget()
        gl = QVBoxLayout(grid_page)
        gl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen: Name, Autor, Beschreibung, Stichwort (z. B. „acid“, „Drums“, „Vestal“)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        row.addWidget(self.search, 1)
        self.sort = QComboBox()
        for k, t in SORTS:
            self.sort.addItem(t, k)
        self.sort.setCurrentIndex(max(0, self.sort.findData(st.get("sort", "az"))))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        row.addWidget(QLabel("Sortieren:"))
        row.addWidget(self.sort)
        gl.addLayout(row)
        frow = QHBoxLayout()
        self.menus: dict[str, set] = {}
        self.menu_buttons = {}
        for key, title, values in (("cat", "Kategorie", catmod.CATEGORIES), ("lin", "Basis", catmod.LINEAGES),
                                   ("lic", "Lizenz", ["offen", "geschlossen"]), ("status", "Status", ["stabil", "Beta"])):
            b = QToolButton()
            b.setText(title)
            b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            mm = QMenu(b)
            self.menus[key] = set()
            for v in values:
                a = QAction(v, mm)
                a.setCheckable(True)
                a.toggled.connect(lambda on, k=key, val=v: self._menu_toggled(k, val, on))
                mm.addAction(a)
            b.setMenu(mm)
            self.menu_buttons[key] = (b, title)
            frow.addWidget(b)
        self.chips = {}
        for key, text, tip in CHIPS:
            c = QPushButton(text)
            c.setCheckable(True)
            c.setToolTip(tip)
            c.setStyleSheet("QPushButton{border-radius:11px;padding:3px 10px;}"
                            "QPushButton:checked{background:palette(highlight);color:palette(highlighted-text);}")
            c.toggled.connect(self.apply_filter)
            self.chips[key] = c
            frow.addWidget(c)
        ic = icon("edit-clear-all", "edit-clear")
        b_reset = QPushButton(ic, "" if not ic.isNull() else "Zurücksetzen")
        b_reset.setToolTip("Alle Filter zurücksetzen")
        b_reset.clicked.connect(self.reset_filters)
        frow.addWidget(b_reset)
        frow.addStretch(1)
        self.count = QLabel()
        frow.addWidget(self.count)
        gl.addLayout(frow)
        reflow(frow)                                     # Filter-Chips brechen bei schmalem Fenster um
        self.model = EntryModel(self)
        self.model.wish = set(st.get("wishlist") or [])
        self.model.thumb_wanted.connect(self.want_thumb)
        self.view = QListView()
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(2)
        self.view.setWrapping(True)
        self.view.setMouseTracking(True)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(24)
        self.delegate = CardDelegate(self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.viewport().installEventFilter(self)
        self.view.setModel(self.model)
        self.view.activated.connect(self._open_index)
        self.view.clicked.connect(self._open_index)
        gl.addWidget(self.view, 1)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setVisible(False)
        gl.addWidget(self.empty, 1)
        self.stack.addWidget(grid_page)
        self.detail = DetailView(self)
        self.detail.back.connect(lambda: self.stack.setCurrentIndex(0))
        self.stack.addWidget(self.detail)
        sl.addWidget(self.stack)
        self.tabs.addTab(store, icon("applications-all", "view-grid"), "Alle Firmwares")
        # ---- Neuigkeiten
        news = QWidget()
        nl = QVBoxLayout(news)
        self.news_info = QLabel()
        self.news_info.setWordWrap(True)
        nl.addWidget(self.news_info)
        self.news = QTreeWidget()
        self.news.setHeaderLabels(["", "Firmware", "Was", "Datum"])
        self.news.setRootIsDecorated(False)
        self.news.itemDoubleClicked.connect(lambda it, _c: self.open_key(it.data(1, Qt.ItemDataRole.UserRole)))
        nl.addWidget(self.news, 1)
        nr = QHBoxLayout()
        self.b_news = QPushButton(icon("view-refresh"), "Auf neue Versionen prüfen")
        self.b_news.setToolTip("fragt GitHub nach den Releases der Firmwares, denen du folgst (höchstens 12)")
        self.b_news.clicked.connect(self.check_news)
        nr.addWidget(self.b_news)
        nr.addStretch(1)
        nl.addLayout(nr)
        self.tabs.addTab(news, icon("view-calendar-upcoming-events", "appointment-new"), "Neuigkeiten")
        # ---- Werkzeuge & Wissen
        res = QWidget()
        rl = QVBoxLayout(res)
        self.res_search = QLineEdit()
        self.res_search.setPlaceholderText("Werkzeuge, Editoren, Rettung, Anleitungen, Videos … durchsuchen")
        self.res_search.setClearButtonEnabled(True)
        self.res_search.textChanged.connect(self.fill_resources)
        rl.addWidget(self.res_search)
        self.res = QTreeWidget()
        self.res.setHeaderLabels(["Name", "Beschreibung"])
        self.res.setWordWrap(True)
        self.res.itemDoubleClicked.connect(self._open_resource)
        rl.addWidget(self.res, 1)
        rl.addWidget(QLabel("Doppelklick öffnet die Seite im Browser. Quelle: awesome-fm-1 (CC0)."))
        self.tabs.addTab(res, icon("help-contents", "tools"), "Werkzeuge && Wissen")

        paste = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self)
        paste.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        paste.activated.connect(self._paste)
        find = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        find.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find.activated.connect(lambda: (self.tabs.setCurrentIndex(0), self.stack.setCurrentIndex(0),
                                        self.search.setFocus()))
        self.set_catalog(self.cat)

    # ------------------------------------------------------------ Katalog
    def set_catalog(self, cat: catmod.Catalog):
        self.cat = cat
        for e in cat.entries:
            g = catmod.cached_enrichment(e.repo) if e.repo else None
            if g:
                self.gh[e.key] = g
                if g.get("stars") is not None:
                    e.stars = g["stars"]
        self._own_entries()
        src = {"snapshot": "mitgelieferter Stand", "cache": "Stand vom letzten Abgleich", "live": "gerade aktualisiert",
               "leer": "kein Katalog"}.get(cat.source, cat.source)
        date = cat.issued[:10] if cat.issued else ""
        date = ".".join(reversed(date.split("-"))) if date else "?"
        n_inst = sum(1 for e in cat.entries if e.installable)
        sub = self.head.findChildren(QLabel)
        text = (f"{len(cat.entries)} Firmwares für das FM-1, {n_inst} davon direkt installierbar. Katalog-Revision "
                f"{cat.revision} vom {date} ({src}). Quellen: <a href='{catmod.AWESOME_URL}'>awesome-fm-1</a> und "
                f"Mooring (cicloid). Mehr Verzeichnisse: <a href='{catmod.HUB_URL}'>FM-1 Firmware Hub</a>, "
                f"<a href='{catmod.DIRECTORY_URL}'>fm1-editor.com/firmware</a>.")
        if len(sub) > 1:
            sub[1].setText(text)
        else:
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setOpenExternalLinks(True)
            lab.setStyleSheet("color: palette(mid);")
            self.head.layout().addWidget(lab)
        self.apply_filter()
        self.fill_news()
        self.fill_resources()

    def _own_entries(self):
        """eigene Einträge (settings.extra_firmwares), die nicht im Katalog stehen, als Karten zeigen."""
        keys = {e.key for e in self.cat.entries}
        self.cat.entries = [e for e in self.cat.entries if e.section != "own"]
        for d in config.load().extra_firmwares:
            if d.get("key") in keys or d.get("catalog_id") in keys:
                continue
            e = catmod.Entry(key=d.get("key", ""), name=d.get("name", "?"), author=d.get("author", ""),
                             description=d.get("description", "") or "Von dir hinzugefügt.", section="own",
                             categories=[d.get("category") or "Synth"], license=d.get("license", ""),
                             repo=d.get("repo", ""), protocol=d.get("protocol", "none"), family=d.get("family", ""),
                             links={k: v for k, v in (("emulator", d.get("emulator_url")), ("editor", d.get("editor_url")),
                                                      ("manual", d.get("manual_url")), ("installer", d.get("installer_url")),
                                                      ("site", d.get("homepage"))) if v})
            if d.get("download_url") and d.get("sha256"):
                e.packages = [catmod.Package(label=d.get("name", ""), url=d["download_url"], sha256=d["sha256"],
                                             product=d.get("product", ""))]
            e.flags.append("eigener Eintrag")
            self.cat.entries.append(e)

    def eventFilter(self, obj, ev):
        if obj is self.view.viewport() and ev.type() == QEvent.Type.Resize:
            self._fit_cards(ev.size().width())
        return super().eventFilter(obj, ev)

    def _fit_cards(self, width: int):
        """Kartenraster nutzt die volle Breite: so viele Spalten wie möglich, Karten gleichmäßig verbreitert."""
        sp = self.view.spacing()
        n = max(1, (width - sp) // (CardDelegate.W + sp))
        w = max(CardDelegate.W, (width - sp * (n + 1)) // n - 1)
        if w != self.delegate.width:
            self.delegate.width = w
            self.view.setGridSize(QSize(w + sp, CardDelegate.H + sp))
            self.view.scheduleDelayedItemsLayout()

    def _set_pref(self, key: str, on: bool):
        settings()[key] = bool(on)
        config.save()
        if key == "thumbnails" and on:
            self._thumb_failed.clear()

    def activate(self):
        if net_allowed() and settings().get("auto_refresh", True) and catmod.needs_refresh() and not self._refreshing:
            self.refresh()

    def deactivate(self):
        st = settings()
        st["last_visit"] = time.time()
        config.save()

    def refresh(self, force: bool = False):
        if self._refreshing or not net_allowed():
            if not net_allowed():
                self._note("Offline-Modus: es gilt der mitgelieferte Katalog.")
            return
        self._refreshing = True
        self.b_refresh.setEnabled(False)
        self.b_refresh.setText("lädt …")

        def done(cat):
            self._refreshing = False
            self.b_refresh.setEnabled(True)
            self.b_refresh.setText("Aktualisieren")
            self.set_catalog(catmod.load())
            try:
                n = len(self.cat.entries)
            except Exception:  # noqa: BLE001
                n = 0
            notify(f"Katalog aktualisiert – {n} Einträge." if n else "Katalog aktualisiert.", "success")

        def fail(msg, tb):
            self._refreshing = False
            self.b_refresh.setEnabled(True)
            self.b_refresh.setText("Aktualisieren")
            from ...dx7_sources import net_error_text      # Netzfehler als deutscher Satz statt „[Errno -3] …“
            age = catmod.cache_age()
            when = f" vom {time.strftime('%d.%m.%Y', time.localtime(time.time() - age))}" if age is not None else ""
            text = (f"Katalog nicht aktualisiert: {net_error_text(msg)} Du siehst weiter den gespeicherten "
                    f"Stand{when}; alles hier geht auch offline.")
            self._note(text)
            if force:                                   # auf Knopfdruck: sichtbar, nicht nur in der Statuszeile
                notify(text, "warning")
        run_job(self, lambda w: catmod.refresh(), on_done=done, on_fail=fail)

    def _note(self, text: str):
        if self.window is not None and hasattr(self.window, "statusBar"):
            self.window.statusBar().showMessage(text, 10000)

    # ------------------------------------------------------------ Filter
    def _menu_toggled(self, key, val, on):
        (self.menus[key].add if on else self.menus[key].discard)(val)
        b, title = self.menu_buttons[key]
        b.setText(title + (f" ({len(self.menus[key])})" if self.menus[key] else ""))
        self.apply_filter()

    def reset_filters(self):
        for key, (b, _t) in self.menu_buttons.items():
            for a in b.menu().actions():
                a.setChecked(False)
        for c in self.chips.values():
            c.setChecked(False)
        self.search.clear()

    def _sort_changed(self):
        settings()["sort"] = self.sort.currentData()
        config.save()
        self.apply_filter()

    def matches(self, e: catmod.Entry) -> bool:
        m = self.menus
        if m["cat"] and not (m["cat"] & set(e.categories)):
            return False
        if m["lin"] and not (m["lin"] & set(e.lineage)):
            return False
        if m["lic"] and not ((("offen" in m["lic"]) and e.open_source) or (("geschlossen" in m["lic"]) and not e.open_source)):
            return False
        if m["status"] and not ((("Beta" in m["status"]) and e.beta) or (("stabil" in m["status"]) and not e.beta)):
            return False
        c = {k: b.isChecked() for k, b in self.chips.items()}
        if c["installable"] and not e.installable:
            return False
        if c["emulator"] and not e.emulator_url:
            return False
        if c["editor"] and not e.editor_url:
            return False
        if c["backup"] and e.protocol != "felucca":
            return False
        if c["wish"] and e.key not in self.model.wish:
            return False
        return True

    def filtered(self) -> list[catmod.Entry]:
        words = self.search.text().lower().split()
        items = [e for e in self.cat.entries if self.matches(e) and all(w in e.search_text() for w in words)]
        mode = self.sort.currentData()
        items.sort(key=lambda e: e.name.lower())
        if mode == "updated":
            items.sort(key=lambda e: entry_date(e, self.gh.get(e.key)), reverse=True)
        elif mode == "stars":
            items.sort(key=lambda e: e.stars if e.stars is not None else -1, reverse=True)
        elif mode == "installable":
            items.sort(key=lambda e: not e.installable)
        return items

    def apply_filter(self):
        items = self.filtered()
        self.model.set_items(items)
        n = len(self.cat.entries)
        self.count.setText(f"{len(items)} von {n}")
        empty = not items
        self.view.setVisible(not empty)
        self.empty.setVisible(empty)
        if empty:
            self.empty.setText("Keine Firmware passt zu Suche und Filtern.\nTipp: „Alle Filter zurücksetzen“ (Knopf rechts neben den Filtern) "
                               "oder weniger Suchwörter." if n else
                               "Der Katalog ist leer – „Aktualisieren“ lädt ihn aus dem Internet.")

    # ------------------------------------------------------------ Vorschaubilder
    def want_thumb(self, key: str):
        if key in self._thumb_queue or key in self._thumb_failed or key in self.model.thumbs:
            return
        e = self.cat.by_key(key)
        if not e or not e.screenshot:
            return
        f = catmod.thumb_file(e.screenshot)
        if f.exists() and f.stat().st_size:
            self._thumb_loaded(key, str(f))
            return
        if not (net_allowed() and settings().get("thumbnails", True)):
            self._thumb_failed.add(key)
            return
        self._thumb_queue.append(key)
        self._pump_thumbs()

    def _pump_thumbs(self):
        while self._thumb_queue and self._thumb_busy < 3:
            key = self._thumb_queue.pop(0)
            e = self.cat.by_key(key)
            if not e:
                continue
            self._thumb_busy += 1
            run_job(self, lambda w, u=e.screenshot: str(catmod.fetch_thumb(u)),
                    on_done=lambda p, k=key: (self._thumb_done(), self._thumb_loaded(k, p)),
                    on_fail=lambda m, tb, k=key: (self._thumb_done(), self._thumb_failed.add(k)))

    def _thumb_done(self):
        self._thumb_busy -= 1
        QTimer.singleShot(0, self._pump_thumbs)

    def _thumb_loaded(self, key: str, path: str):
        pm = QPixmap(path)
        if pm.isNull():
            self._thumb_failed.add(key)
            return
        if pm.width() > 480:
            pm = pm.scaledToWidth(480, Qt.TransformationMode.SmoothTransformation)
        self.model.set_thumb(key, pm)
        if self.detail.entry and self.detail.entry.key == key:
            self.detail.set_pic(pm)

    # ------------------------------------------------------------ Detail
    def _open_index(self, idx):
        e = idx.data(Qt.ItemDataRole.UserRole)
        if e:
            self.open_entry(e)

    def open_entry(self, e: catmod.Entry):
        self.tabs.setCurrentIndex(0)
        self.detail.show_entry(e)
        self.stack.setCurrentIndex(1)

    def open_key(self, key: str):
        e = self.cat.by_key(key or "")
        if e:
            self.open_entry(e)

    def open_with(self, key: str = "", search: str = "", link: str = "", **_kw):
        if key:
            self.open_key(key)
        elif search:
            self.tabs.setCurrentIndex(0)
            self.stack.setCurrentIndex(0)
            self.search.setText(search)
        elif link:
            self.link_import(link)

    def load_github(self, e: catmod.Entry):
        def done(info, key=e.key):
            self.gh[key] = info
            if info.get("stars") is not None:
                ent = self.cat.by_key(key)
                if ent:
                    ent.stars = info["stars"]
            self.detail.github_loaded(key, info, info.get("message", ""))

        def fail(msg, tb, key=e.key):
            self.detail.github_loaded(key, None, f"GitHub: {msg}")
        run_job(self, lambda w: catmod.enrich(e.repo), on_done=done, on_fail=fail)

    # ------------------------------------------------------------ Aktionen
    @property
    def current(self) -> catmod.Entry | None:
        return self.detail.entry

    def install(self):
        e = self.current
        if not e:
            return
        spec = e.to_spec()
        page = self.window.page("firmware") if self.window is not None else None
        if page is not None and hasattr(page, "open_with"):
            self.window.go("firmware", install=spec)
            return
        box = QMessageBox(QMessageBox.Icon.Information, "Installieren",
                          f"Das Aufspielen direkt aus dem Katalog kommt mit dem neuen Installer.\n\n"
                          f"Bis dahin: „Zu meinen Firmwares“ – dann steht {e.name} im Reiter „Installieren & "
                          f"Versionen“ bereit"
                          + (", oder den Web-Installer des Autors im Browser öffnen." if e.links.get("installer") else "."),
                          parent=self)
        add = box.addButton("Zu meinen Firmwares", QMessageBox.ButtonRole.AcceptRole)
        web = box.addButton("Web-Installer öffnen", QMessageBox.ButtonRole.ActionRole) if e.links.get("installer") else None
        box.addButton("Schließen", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is add:
            self.add_own()
        elif web is not None and box.clickedButton() is web:
            QDesktopServices.openUrl(QUrl(e.links["installer"]))

    def _web(self, url: str, kind: str):
        e = self.current
        if not (e and url):
            return
        title = f"{e.name} – {'Simulator' if kind == 'emulator' else 'Editor'}"
        if self.window is not None and self.window.page("simulator") is not None:
            self.window.go("simulator", url=url, title=title, kind=kind)
        else:
            QDesktopServices.openUrl(QUrl(url))
            self._note(f"{title} im Browser geöffnet.")

    def emulator(self):
        self._web(self.current.emulator_url if self.current else "", "emulator")

    def editor(self):
        self._web(self.current.editor_url if self.current else "", "editor")

    def add_own(self):
        e = self.current
        if not e:
            return
        if e.builtin:
            self._note(f"{e.name} ist schon fest eingebaut – du findest es im Reiter „Installieren & Versionen“.")
            QMessageBox.information(self, "Schon da", f"{e.name} ist fest in der App eingebaut.")
            return
        from ... import linkimport as L
        d = L.empty_draft()
        L._from_entry(d, e)
        d.key = e.key if not any(s.key == e.key and s.builtin for s in firmware.catalog()) else L.unique_key(e.key)
        L.save(d, editing_key=e.key)
        self.detail.b_add.setText("In meinen Firmwares ✓")
        self._note(f"{e.name} steht jetzt im Reiter „Installieren & Versionen“.")

    def toggle_wish(self, on: bool):
        e = self.current
        if not e:
            return
        (self.model.wish.add if on else self.model.wish.discard)(e.key)
        settings()["wishlist"] = sorted(self.model.wish)
        config.save()
        self.view.viewport().update()
        self.fill_news()

    # ------------------------------------------------------------ Link-Import
    def _paste(self):
        txt = QGuiApplication.clipboard().text()
        if _looks_like_link(txt) or (txt.strip() and len(txt) < 80):
            self.link_import(txt)
        else:
            self.link_import("")

    def link_import(self, text: str = ""):
        d = LinkImportDialog(self, text, cat=self.cat, online=net_allowed())
        if d.exec() and d.saved_spec is not None:
            self.set_catalog(self.cat)
            self._note(f"{d.saved_spec.name} gespeichert – steht jetzt im Reiter „Installieren & Versionen“.")
        return d

    def own_dialog(self):
        d = OwnFirmwaresDialog(self, self.cat)
        d.exec()
        if d.changed:
            self.set_catalog(self.cat)

    def token_dialog(self):
        st = settings()
        tok, ok = QInputDialog.getText(self, "GitHub-Token",
                                       "Optional: persönlicher GitHub-Token (nur Lesezugriff auf öffentliche Repos "
                                       "nötig).\nOhne Token erlaubt GitHub 60 Abfragen pro Stunde, mit Token 5000.\n"
                                       "Leer lassen zum Entfernen.", QLineEdit.EchoMode.Password, st.get("github_token", ""))
        if ok:
            st["github_token"] = tok.strip()
            config.save()

    # ------------------------------------------------------------ Neuigkeiten
    def followed(self) -> dict[str, str]:
        """key -> Grund"""
        out = {}
        for k in self.model.wish:
            out[k] = "gemerkt"
        for d in config.load().extra_firmwares:
            out.setdefault(d.get("catalog_id") or d.get("key"), "deine Firmware")
        try:
            for p in self.ctx.store.profiles.values():
                if p.firmware:
                    out[p.firmware] = f"auf {p.name}"
        except AttributeError:
            pass
        return {k: v for k, v in out.items() if k}

    def fill_news(self):
        self.news.clear()
        fol = self.followed()
        rows = []
        for c in catmod.recorded_changes():
            rows.append((c.get("date", "")[:10], c.get("key"), c.get("text", ""), "Katalog"))
        for key, why in fol.items():
            g = self.gh.get(key)
            e = self.cat.by_key(key)
            if not (g and e):
                continue
            for r in g.get("releases", [])[:3]:
                rows.append((r["date"], key, f"{e.name} {r['tag']}" + (" (Vorabversion)" if r["prerelease"] else ""),
                             why))
        rows.sort(key=lambda r: r[0], reverse=True)
        last = time.strftime("%Y-%m-%d", time.localtime(self.last_visit)) if self.last_visit else ""
        for date, key, text, why in rows[:200]:
            new = bool(last and date and date > last)
            e = self.cat.by_key(key or "")
            it = QTreeWidgetItem(["neu" if new else "", e.name if e else key, f"{text} · {why}",
                                  ".".join(reversed(date.split("-"))) if date else ""])
            it.setData(1, Qt.ItemDataRole.UserRole, key)
            if new:
                f = it.font(0)
                f.setBold(True)
                for c in range(4):
                    it.setFont(c, f)
                it.setForeground(0, QColor("#2e9d4f"))
            self.news.addTopLevelItem(it)
        for c in range(4):
            self.news.resizeColumnToContents(c)
        names = ", ".join(self.cat.by_key(k).name for k in list(fol)[:6] if self.cat.by_key(k))
        if not rows:
            self.news_info.setText("Noch keine Neuigkeiten. So geht's: Firmwares mit „Merken“ markieren oder zu deinen "
                                   "Firmwares hinzufügen – dann zeigt die App hier neue Versionen. Firmwares auf deinen "
                                   "FM-1 verfolgt sie automatisch." + (f"\nDu folgst: {names}." if names else ""))
        else:
            self.news_info.setText(f"Neue Versionen der Firmwares, denen du folgst ({names or 'keine'}), und Änderungen "
                                   "im Katalog. „neu“ = seit deinem letzten Besuch. Doppelklick öffnet die Firmware.")

    def check_news(self):
        if not net_allowed():
            self.news_info.setText("Offline – Neuigkeiten kommen beim nächsten Mal.")
            return
        repos = [(k, self.cat.by_key(k).repo) for k in self.followed() if self.cat.by_key(k) and self.cat.by_key(k).repo]
        if not repos:
            self.fill_news()
            return
        self.b_news.setEnabled(False)

        def work(w):
            out, msg = {}, ""
            for k, r in repos[:12]:
                try:
                    out[k] = catmod.enrich(r)
                    msg = msg or out[k].get("message", "")
                except catmod.RateLimited as e:
                    return out, str(e)
                except catmod.Offline as e:
                    msg = str(e)
            return out, msg

        def done(res):
            out, msg = res
            self.gh.update(out)
            self.b_news.setEnabled(True)
            self.fill_news()
            if msg:
                self.news_info.setText(self.news_info.text() + "\n" + msg)
        run_job(self, work, on_done=done, on_fail=lambda m, tb: (self.b_news.setEnabled(True),
                                                                   self.news_info.setText(f"GitHub: {m}")))

    # ------------------------------------------------------------ Werkzeuge & Wissen
    def fill_resources(self):
        self.res.clear()
        words = self.res_search.text().lower().split()
        groups: dict[str, QTreeWidgetItem] = {}
        names = {"Editors, librarians and companion software": "Editoren und Begleit-Software",
                 "Firmware update and recovery": "Update und Rettung", "Patches and sound banks": "Sounds und Patches",
                 "Hardware add-ons": "Hardware-Zubehör", "Documentation and guides": "Anleitungen",
                 "Articles and reviews": "Artikel und Tests", "Videos": "Videos", "Communities": "Communities",
                 "Reverse engineering and firmware development": "Entwicklung und Reverse Engineering",
                 "Related M-VAVE projects": "Verwandte M-VAVE-Projekte"}
        for r in self.cat.resources:
            if words and not all(w in (r.name + " " + r.description + " " + r.section).lower() for w in words):
                continue
            g = groups.get(r.section)
            if g is None:
                g = QTreeWidgetItem([names.get(r.section, r.section), ""])
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                g.setFirstColumnSpanned(False)
                self.res.addTopLevelItem(g)
                groups[r.section] = g
            it = QTreeWidgetItem([r.name, r.description])
            it.setToolTip(1, r.description)
            it.setData(0, Qt.ItemDataRole.UserRole, r.url)
            g.addChild(it)
        self.res.expandAll() if words else [g.setExpanded(i < 3) for i, g in enumerate(groups.values())]
        self.res.setColumnWidth(0, 260)

    def _open_resource(self, it, _col):
        url = it.data(0, Qt.ItemDataRole.UserRole)
        if url:
            QDesktopServices.openUrl(QUrl(url))


def _make(ctx, window):
    return DiscoverPage(ctx, window)


PAGE = PageSpec(key="discover", title="Firmware+", group="daten", factory=_make,
                icons=("system-software-update", "plasmadiscover", "system-software-install", "applications-all"),
                order=12, description="Alle FM-1-Firmwares an einem Ort: finden, ausprobieren, installieren, "
                                      "Versionen wechseln, zurück zum Original",
                keywords=("firmware", "firmwares", "entdecken", "katalog", "store", "installieren", "update",
                          "version", "emulator", "link", "awesome-fm-1", "mooring", "hub"))
