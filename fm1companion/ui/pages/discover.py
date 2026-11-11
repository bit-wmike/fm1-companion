# SPDX-License-Identifier: GPL-3.0-only
"""Firmware+ (formerly “Discover”): all known FM-1 firmwares like in an app store – search, filter,
view, try in the simulator, install. Plus “Paste link …", news and tools & knowledge.
The main window embeds the former “Firmware” page (ui/firmware_page.py) as the “Install & versions” tab
(main_window.FLAT_COMPOSITES) – installing, switching versions and the way back to the original live there.

Data: catalog.py (bundled snapshot → live state every 24 h → GitHub cache).
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
from ... import config, firmware, fwfamily
from ..common import notify, fmt_size, heading, icon, run_job
from ..responsive import reflow
from ..linkimport_dialog import LinkImportDialog, OwnFirmwaresDialog, _looks_like_link
from . import PageSpec
from ...i18n import N_, _, ngettext, pgettext

# the backup protocols the app speaks (fwfamily: what the catalog audit found in a firmware's source code)
BACKUP_PROTOCOL_NAMES = {"felucca": "Felucca", "sloop": "SLOOP", "x0x": "X0X"}
CAT_COLORS = {"Original": "#8e44ad", "Synth": "#2a7ab8", "Groovebox": "#e67e22", "Sampler": "#16a085",  # i18n: ignore
              "Akkorde": "#c0398b", "Generativ": "#27ae60", "Werkzeug": "#7f8c8d", "Spiele & Spaß": "#d35400"}  # i18n: ignore
SORTS = [("az", "A–Z"), ("updated", _("recently updated")), ("stars", _("popularity (stars)")),
         ("installable", _("directly installable first"))]
CHIPS = [("installable", _("Directly installable"), _("Package with checksum in the catalog – the app can "
                                                   "install it")),
         ("emulator", _("With simulator"), _("try it out in the browser without installing anything")),
         ("editor", _("With editor"), _("has an online editor")),
         ("backup", _("Backup-capable"), _("Felucca protocol: backups, projects and editor bridge in the app")),
         ("wish", _("Remembered"), _("your wishlist"))]
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


# ================================================================ Model ===

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
            own = e.section == "own" or catmod.is_app_description(e)
            return (f"<b>{e.name}</b><br>" + ("" if own else f"<i>{_(EN_LABEL)}:</i><br>")
                    + catmod.description_text(e)[:300])
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
    out = [(catmod.category_name(c), CAT_COLORS.get(c, "#888")) for c in e.categories[:1]]
    if e.lineage:
        out.append((catmod.lineage_name(e.lineage[-1]), "#5d6d7e"))
    if e.installable:
        out.append((_("installable"), "#2e9d4f"))
    if e.beta:
        out.append((_("Beta"), "#c77700"))
    if e.section == "closed":
        out.append((pgettext("license", "closed"), "#7f8c8d"))
    if "kostenpflichtig" in e.flags:  # i18n: ignore
        out.append((_("paid"), "#b03a2e"))
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


EN_LABEL = N_("Author's description")


class CardDelegate(QStyledItemDelegate):
    W, H, T = 300, 150, 84                      # W: minimum width; the actual width fills the row

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
        by = _("by {author}").format(author=e.author) if e.author else ""
        if e.stars:
            by += f"   ★ {e.stars}"
        p.drawText(QRect(x, y, maxx - x, sm.height()), Qt.AlignmentFlag.AlignLeft,
                   sm.elidedText(by, Qt.TextElideMode.ElideRight, maxx - x))
        y += sm.height() + 3
        if e.section != "own" and e.description and not catmod.is_app_description(e):
            ital = QFont(small)
            ital.setItalic(True)
            p.setFont(ital)
            p.drawText(QRect(x, y, maxx - x, sm.height()), Qt.AlignmentFlag.AlignLeft,
                       QFontMetrics(ital).elidedText(_(EN_LABEL) + ":", Qt.TextElideMode.ElideRight, maxx - x))
            p.setFont(small)
            y += sm.height() + 1
        p.setPen(pal.color(QPalette.ColorRole.Text))
        desc_rect = QRect(x, y, maxx - x, sm.height() * 2 + 2)
        lines = _two_lines(catmod.description_text(e), sm, maxx - x)
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
                rest = i                        # from here on nothing fits anymore: only append the rest truncated
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
        b = QPushButton(icon("go-previous"), _("All firmwares"))
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
        # ---- Header
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
        lineage = " › ".join(catmod.lineage_name(x) for x in e.lineage)
        sub = [_("by <b>{author}</b>").format(author=e.author) if e.author else "",
               e.license and _("License: {license}").format(license=catmod.license_name(e.license)),
               lineage and _("Based on: {lineage}").format(lineage=lineage)]
        if e.stars:
            sub.append(_("★ {stars} on GitHub").format(stars=e.stars))
        s = QLabel(" · ".join(x for x in sub if x))
        s.setWordWrap(True)
        info.addWidget(s)
        bl = QLabel(" ".join(f"<span style='background:rgba({QColor(c).red()},{QColor(c).green()},"
                             f"{QColor(c).blue()},0.18);color:{c}'>&nbsp;{t}&nbsp;</span>" for t, c in badges(e)))
        bl.setTextFormat(Qt.TextFormat.RichText)
        info.addWidget(bl)
        acts = QHBoxLayout()
        self.b_install = _btn(_("Install …"), ("system-software-update", "go-down"), self.page.install,
                              _("install on the active FM-1 (with confirmation and backup)"), primary=True)
        self.b_install.setEnabled(e.installable or e.builtin or bool(e.repo))
        if not (e.installable or e.builtin):
            self.b_install.setToolTip(_("No verified package in the catalog – the app looks for the firmware "
                                        "file in the GitHub releases (you check it before saving)") if e.repo else
                                      _("No verified package in the catalog – see the author's "
                                        "website/installer"))
        acts.addWidget(self.b_install)
        if e.emulator_url:
            acts.addWidget(_btn(_("Try in the simulator"), ("media-playback-start",), self.page.emulator,
                                _("runs on the computer – nothing is written to the FM-1")))
        if e.editor_url:
            acts.addWidget(_btn(_("Open online editor"), ("document-edit", "audio-midi"), self.page.editor))
        self.b_add = _btn(_("Add to my firmwares"), ("list-add",), self.page.add_own,
                          _("then also appears in the tab “Install & versions”"))
        acts.addWidget(self.b_add)
        self.b_wish = QPushButton(icon("bookmark-new", "starred", "emblem-favorite"), _("Remember"))
        self.b_wish.setCheckable(True)
        self.b_wish.setChecked(e.key in self.page.model.wish)
        self.b_wish.toggled.connect(self.page.toggle_wish)
        acts.addWidget(self.b_wish)
        more = QToolButton()
        more.setText(_("Links"))
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
        reflow(acts)                                     # buttons wrap when the window is narrow
        info.addStretch(1)
        head.addLayout(info, 1)
        lay.addLayout(head)

        # ---- Warnings
        warns = e.warnings()
        if warns:
            w = QLabel("<b>" + _("Good to know") + "</b><br>" + "<br>".join(f"⚠ {x}" for x in warns))
            w.setWordWrap(True)
            w.setTextFormat(Qt.TextFormat.RichText)
            w.setStyleSheet("background:rgba(230,126,34,0.13);border:1px solid rgba(230,126,34,0.5);"
                            "border-radius:8px;padding:8px;")
            lay.addWidget(w)
            self.labels["warnings"] = w
        # ---- Description
        d = QLabel(catmod.description_text(e) + ("" if e.section == "own" or catmod.is_app_description(e) else
                                                 "<br><span style='color:gray'>"
                                                 + _("Author's description, from awesome-fm-1 – deliberately not "
                                                     "translated") + "</span>"))
        d.setWordWrap(True)
        d.setTextFormat(Qt.TextFormat.RichText)
        d.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(_section(_("Description"), d))
        # ---- Matrix
        lay.addWidget(_section(_("What the app can do with it"), self.matrix(e)))
        # ---- Versions
        self.versions = QTreeWidget()
        self.versions.setHeaderLabels([_("Version"), _("Date"), _("Size"), _("Notes")])
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
        lay.addWidget(_section(_("Versions"), vb))
        # ---- README
        self.readme = QTextBrowser()
        self.readme.setOpenExternalLinks(True)
        self.readme.setMinimumHeight(260)
        self.readme.setVisible(False)
        rb = QWidget()
        rl = QVBoxLayout(rb)
        rl.setContentsMargins(0, 0, 0, 0)
        self.b_readme = QPushButton(icon("document-preview", "text-x-generic"), _("Load the project's README"))
        self.b_readme.setEnabled(bool(e.repo))
        self.b_readme.clicked.connect(self.load_readme)
        rl.addWidget(self.b_readme, 0, Qt.AlignmentFlag.AlignLeft)
        rl.addWidget(self.readme)
        lay.addWidget(_section(_("From the author"), rb))
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
        names = {"installer": _("Author's web installer"), "emulator": _("Simulator in the browser"),
                 "editor": _("Online editor"), "manual": _("Manual"), "site": _("Website"), "source": _("Source code"),
                 "downloads": _("Downloads (M-VAVE)"), "release_notes": _("Release notes (M-VAVE)"),
                 "midi": _("MIDI reference (M-VAVE)")}
        out = []
        if e.repo:
            out.append((_("View on GitHub"), e.repo_url))
        for k, u in e.links.items():
            if not k.startswith("firmware_"):
                out.append((names.get(k, k), u))
        out.append((_("View on thefirmwarehub.com"), f"{catmod.HUB_URL}#{e.hub_anchor}"))
        out.append((_("View in the FM1 firmware directory"), f"{catmod.DIRECTORY_URL}#{e.hub_anchor}"))
        return out

    def matrix(self, e: catmod.Entry) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        if e.installable or e.builtin:
            inst = ("✓", _("Install via M-VAVE's standard update protocol – the checksum is verified before "
                           "installing") if e.installable else _("Install via M-VAVE's standard update protocol"))
        elif "kostenpflichtig" in e.flags or e.section == "closed":
            inst = ("–", _("Install only via the manufacturer's site"))
        else:
            inst = ("–", _("No verified package in the catalog; enter a .fwsc address via “Paste link”"))
        fam = fwfamily.by_key(e.key)
        if e.protocol == "felucca":
            bk = ("✓", _("Backups, projects and editor bridge (Felucca protocol; {evidence})").format(
                evidence=catmod.protocol_evidence_text(e)))
        elif fam is not None and fam.backup in BACKUP_PROTOCOL_NAMES:
            bk = ("✓", _("Backups through the app ({protocol} backup protocol, from its source code)").format(
                protocol=BACKUP_PROTOCOL_NAMES[fam.backup]))
        elif fam is not None and fam.backup == "own":
            bk = ("✗", _("Backups only with the firmware's own editor (a backup protocol of its own)"))
        elif fam is not None:
            bk = ("✗", _("No backup over USB"))
        else:
            bk = ("?", _("Backups/projects: unknown – checked when connecting"))
        # (key for self.labels, title, mark, text)
        rows = [("Installieren", _("Install"), *inst), ("Backup & Projekte", _("Backup & projects"), *bk),  # i18n: ignore
                ("Editor", _("Editor"), "✓" if e.editor_url or e.protocol == "felucca" else "✗",
                 _("Online editor in the app") if e.editor_url else (_("Editor from the repo") if e.protocol == "felucca"
                                                                     else _("no editor known"))),
                ("Emulator", _("Emulator"), "✓" if e.emulator_url else "✗",
                 _("try it out on the computer") if e.emulator_url else _("no browser emulator known"))]
        colors = {"✓": "#2e9d4f", "✗": "#888", "?": "#c77700", "–": "#888"}
        for i, (key, name, mark, text) in enumerate(rows):
            m = QLabel(f"<b style='color:{colors[mark]};font-size:14pt'>{mark}</b>")
            g.addWidget(m, i, 0)
            g.addWidget(QLabel(f"<b>{name}</b>"), i, 1)
            lt = QLabel(text)
            lt.setWordWrap(True)
            g.addWidget(lt, i, 2)
            self.labels[f"m_{key}"] = mark
        g.setColumnStretch(2, 1)
        return w

    def fill_versions(self, e, gh: dict | None):
        self.versions.clear()
        seen = set()
        for r in (gh or {}).get("releases", []):
            fw = [a for a in r["assets"] if a["name"].lower().endswith(".fwsc")]
            size = fmt_size(fw[0]["size"]) if fw else ""
            pkg = _("Package: {name}").format(name=fw[0]["name"]) if fw else _("no .fwsc package")
            it = QTreeWidgetItem([r["tag"], r["date"], size,
                                  _("Pre-release · {package}").format(package=pkg) if r["prerelease"] else pkg])
            self.versions.addTopLevelItem(it)
            seen.add(r["tag"])
        for p in e.packages:
            if p.version in seen:
                continue
            seen.add(p.version)
            note = _("in the Mooring catalog, checksum known") if p.downloadable else _("known package "
                                                                                     "(recognition only)")
            short = {"beta": _("Beta"), "powerLossNotResumable": _("interruption not resumable"),
                     "projectsNotBackwardCompatible": _("projects not backward compatible"),
                     "needsMiniLab3": _("needs MiniLab 3")}
            if p.notes:
                note += " · " + ", ".join(short.get(n, n) for n in p.notes)
            self.versions.addTopLevelItem(QTreeWidgetItem([p.version or p.label, "", fmt_size(p.raw_size), note]))
        if not self.versions.topLevelItemCount():
            self.versions.addTopLevelItem(QTreeWidgetItem([_("no versions known")]))
        for c in range(4):
            self.versions.resizeColumnToContents(c)

    def github_loaded(self, key, info: dict | None, msg: str):
        if not self.entry or self.entry.key != key:
            return
        if info:
            self.fill_versions(self.entry, info)
        self.gh_state.setText(msg or (_("State from GitHub") if info else ""))

    def load_readme(self):
        e = self.entry
        self.b_readme.setEnabled(False)
        self.b_readme.setText(_("loading …"))

        def done(txt, key=e.key):
            if self.entry and self.entry.key == key:
                self.readme.setMarkdown(txt)
                self.readme.setVisible(True)
                self.b_readme.setVisible(False)

        def fail(msg, tb, key=e.key):
            if self.entry and self.entry.key == key:
                from ...dx7_sources import net_error_text
                self.b_readme.setText(_("README not loaded – try again"))
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


# ================================================================ Page ===

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
        self.b_link = QPushButton(icon("edit-paste", "insert-link"), _("Paste link …"))
        self.b_link.setToolTip(_("Paste a GitHub link, .fwsc package, installer or simulator page (Ctrl+V)"))
        f = self.b_link.font()
        f.setBold(True)
        self.b_link.setFont(f)
        self.b_link.clicked.connect(lambda: self.link_import(""))
        self.b_refresh = QPushButton(icon("view-refresh"), _("Refresh"))
        self.b_refresh.setToolTip(_("Reload the catalog (awesome-fm-1 and Mooring)"))
        self.b_refresh.clicked.connect(lambda: self.refresh(force=True))
        self.b_menu = QToolButton()
        self.b_menu.setIcon(icon("application-menu", "configure", "open-menu"))
        self.b_menu.setToolTip(_("More"))
        if self.b_menu.icon().isNull():
            self.b_menu.setText(_("More"))
        self.b_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(self.b_menu)
        m.addAction(icon("folder"), _("My firmwares …"), self.own_dialog)
        m.addAction(icon("dialog-password", "security-high"), _("GitHub token …"), self.token_dialog)
        self.a_clear_wish = m.addAction(icon("edit-clear-all", "edit-clear"), _("Clear the remembered list …"),
                                        self.clear_wishlist)
        m.addSeparator()
        # automatic network access of this page – both can be turned off (privacy, see README)
        self.a_auto = m.addAction(_("Update the catalog automatically when opening"))
        self.a_auto.setCheckable(True)
        self.a_auto.setChecked(bool(settings().get("auto_refresh", True)))
        self.a_auto.toggled.connect(lambda on: self._set_pref("auto_refresh", on))
        self.a_thumbs = m.addAction(_("Load preview images from the internet"))
        self.a_thumbs.setCheckable(True)
        self.a_thumbs.setChecked(bool(settings().get("thumbnails", True)))
        self.a_thumbs.toggled.connect(lambda on: self._set_pref("thumbnails", on))
        m.addSeparator()
        m.addAction(_("awesome-fm-1 on GitHub"), lambda: QDesktopServices.openUrl(QUrl(catmod.AWESOME_URL)))
        m.addAction("FM-1 Firmware Hub", lambda: QDesktopServices.openUrl(QUrl(catmod.HUB_URL)))
        m.addAction(_("FM1 firmware directory"), lambda: QDesktopServices.openUrl(QUrl(catmod.DIRECTORY_URL)))
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
        self.search.setPlaceholderText(_("Search: name, author, description, keyword (e.g. “acid”, "
                                         "“drums”, “Vestal”)"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        row.addWidget(self.search, 1)
        self.sort = QComboBox()
        for k, t in SORTS:
            self.sort.addItem(t, k)
        self.sort.setCurrentIndex(max(0, self.sort.findData(st.get("sort", "az"))))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        row.addWidget(QLabel(_("Sort:")))
        row.addWidget(self.sort)
        gl.addLayout(row)
        frow = QHBoxLayout()
        self.menus: dict[str, set] = {}
        self.menu_buttons = {}
        # filter values are internal keys; the menu shows their display names
        value_names = {"offen": pgettext("license", "open"),  # i18n: ignore
                       "geschlossen": pgettext("license", "closed"),  # i18n: ignore
                       "stabil": _("stable"), "Beta": _("Beta")}  # i18n: ignore
        for key, title, values in (("cat", _("Category"), catmod.CATEGORIES), ("lin", _("Based on"), catmod.LINEAGES),
                                   ("lic", _("License"), ["offen", "geschlossen"]),  # i18n: ignore
                                   ("status", pgettext("release state", "Status"), ["stabil", "Beta"])):  # i18n: ignore
            b = QToolButton()
            b.setText(title)
            b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            mm = QMenu(b)
            self.menus[key] = set()
            for v in values:
                a = QAction(value_names.get(v) or catmod.category_name(catmod.lineage_name(v)), mm)
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
        b_reset = QPushButton(ic, "" if not ic.isNull() else _("Reset"))
        b_reset.setToolTip(_("Reset all filters"))
        b_reset.clicked.connect(self.reset_filters)
        frow.addWidget(b_reset)
        frow.addStretch(1)
        self.count = QLabel()
        frow.addWidget(self.count)
        gl.addLayout(frow)
        reflow(frow)                                     # filter chips wrap when the window is narrow
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
        self.tabs.addTab(store, icon("applications-all", "view-grid"), _("All firmwares"))
        # ---- News
        news = QWidget()
        nl = QVBoxLayout(news)
        self.news_info = QLabel()
        self.news_info.setWordWrap(True)
        nl.addWidget(self.news_info)
        self.news = QTreeWidget()
        self.news.setHeaderLabels(["", _("Firmware"), _("What"), _("Date")])
        self.news.setRootIsDecorated(False)
        self.news.itemDoubleClicked.connect(lambda it, _c: self.open_key(it.data(1, Qt.ItemDataRole.UserRole)))
        nl.addWidget(self.news, 1)
        nr = QHBoxLayout()
        self.b_news = QPushButton(icon("view-refresh"), _("Check for new versions"))
        self.b_news.setToolTip(_("asks GitHub for the releases of the firmwares you follow (at most 12)"))
        self.b_news.clicked.connect(self.check_news)
        nr.addWidget(self.b_news)
        nr.addStretch(1)
        nl.addLayout(nr)
        self.tabs.addTab(news, icon("view-calendar-upcoming-events", "appointment-new"), _("News"))
        # ---- Tools & knowledge
        res = QWidget()
        rl = QVBoxLayout(res)
        self.res_search = QLineEdit()
        self.res_search.setPlaceholderText(_("Search tools, editors, recovery, guides, videos …"))
        self.res_search.setClearButtonEnabled(True)
        self.res_search.textChanged.connect(self.fill_resources)
        rl.addWidget(self.res_search)
        self.res = QTreeWidget()
        self.res.setHeaderLabels([_("Name"), _("Description")])
        self.res.setWordWrap(True)
        self.res.itemDoubleClicked.connect(self._open_resource)
        rl.addWidget(self.res, 1)
        rl.addWidget(QLabel(_("Double-click opens the page in the browser. Source: awesome-fm-1 (CC0).")))
        self.tabs.addTab(res, icon("help-contents", "tools"), _("Tools && knowledge"))

        paste = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self)
        paste.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        paste.activated.connect(self._paste)
        find = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        find.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find.activated.connect(lambda: (self.tabs.setCurrentIndex(0), self.stack.setCurrentIndex(0),
                                        self.search.setFocus()))
        self.set_catalog(self.cat)

    # ------------------------------------------------------------ Catalog
    def set_catalog(self, cat: catmod.Catalog):
        self.cat = cat
        self._apply_stars(catmod.cached_stars())
        for e in cat.entries:
            g = catmod.cached_enrichment(e.repo) if e.repo else None
            if g:
                self.gh[e.key] = g
                if g.get("stars") is not None:
                    e.stars = g["stars"]
        self._own_entries()
        src = {"snapshot": _("bundled state"), "cache": _("state of the last sync"), "live": _("just updated"),
               "leer": _("no catalog")}.get(cat.source, cat.source)
        date = cat.issued[:10] if cat.issued else ""
        date = ".".join(reversed(date.split("-"))) if date else "?"
        n_inst = sum(1 for e in cat.entries if e.installable)
        sub = self.head.findChildren(QLabel)
        text = _("{n} firmwares for the FM-1, {installable} of them directly installable. Catalog revision "
                 "{revision} of {date} ({source}). Sources: <a href='{awesome}'>awesome-fm-1</a> and Mooring "
                 "(cicloid). More directories: <a href='{hub}'>FM-1 Firmware Hub</a>, "
                 "<a href='{directory}'>fm1-editor.com/firmware</a>.").format(
            n=len(cat.entries), installable=n_inst, revision=cat.revision, date=date, source=src,
            awesome=catmod.AWESOME_URL, hub=catmod.HUB_URL, directory=catmod.DIRECTORY_URL)
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
        """Show custom entries (settings.extra_firmwares) that are not in the catalog as cards."""
        keys = {e.key for e in self.cat.entries}
        self.cat.entries = [e for e in self.cat.entries if e.section != "own"]
        for d in config.load().extra_firmwares:
            if d.get("key") in keys or d.get("catalog_id") in keys:
                continue
            e = catmod.Entry(key=d.get("key", ""), name=d.get("name", "?"), author=d.get("author", ""),
                             description=d.get("description", "") or _("Added by you."), section="own",
                             categories=[d.get("category") or "Synth"], license=d.get("license", ""),
                             repo=d.get("repo", ""), protocol=d.get("protocol", "none"), family=d.get("family", ""),
                             links={k: v for k, v in (("emulator", d.get("emulator_url")), ("editor", d.get("editor_url")),
                                                      ("manual", d.get("manual_url")), ("installer", d.get("installer_url")),
                                                      ("site", d.get("homepage"))) if v})
            if d.get("download_url") and d.get("sha256"):
                e.packages = [catmod.Package(label=d.get("name", ""), url=d["download_url"], sha256=d["sha256"],
                                             product=d.get("product", ""))]
            e.flags.append("eigener Eintrag")  # i18n: ignore
            self.cat.entries.append(e)

    def eventFilter(self, obj, ev):
        if obj is self.view.viewport() and ev.type() == QEvent.Type.Resize:
            self._fit_cards(ev.size().width())
        return super().eventFilter(obj, ev)

    def _fit_cards(self, width: int):
        """The card grid uses the full width: as many columns as possible, cards widened evenly."""
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
        if self.sort.currentData() in ("stars", "updated") and settings().get("auto_refresh", True):
            self.load_stars()

    def deactivate(self):
        st = settings()
        st["last_visit"] = time.time()
        config.save()

    def refresh(self, force: bool = False):
        if self._refreshing or not net_allowed():
            if not net_allowed():
                self._note(_("Offline mode: the bundled catalog applies."))
            return
        self._refreshing = True
        self.b_refresh.setEnabled(False)
        self.b_refresh.setText(_("Loading …"))

        def done(cat):
            self._refreshing = False
            self.b_refresh.setEnabled(True)
            self.b_refresh.setText(_("Refresh"))
            self.set_catalog(catmod.load())
            try:
                n = len(self.cat.entries)
            except Exception:  # noqa: BLE001
                n = 0
            notify(ngettext("Catalog updated – {n} entry.", "Catalog updated – {n} entries.", n).format(n=n) if n
                   else _("Catalog updated."), "success")

        def fail(msg, tb):
            self._refreshing = False
            self.b_refresh.setEnabled(True)
            self.b_refresh.setText(_("Refresh"))
            from ...dx7_sources import net_error_text      # network error as a readable sentence instead of “[Errno -3] …”
            age = catmod.cache_age()
            if age is not None:
                text = _("Catalog not updated: {error} You still see the saved state of {date}; everything here also "
                         "works offline.").format(error=net_error_text(msg),
                                                  date=time.strftime("%d.%m.%Y", time.localtime(time.time() - age)))
            else:
                text = _("Catalog not updated: {error} You still see the saved state; everything here also works "
                         "offline.").format(error=net_error_text(msg))
            self._note(text)
            if force:                                   # on button press: visible, not only in the status line
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
        if self.sort.currentData() in ("stars", "updated"):
            self.load_stars()

    def _apply_stars(self, stars: dict) -> int:
        n = 0
        for e in self.cat.entries:
            s = stars.get((e.repo or "").lower())
            if not s:
                continue
            if s.get("stars") is not None:
                e.stars = s["stars"]
                n += 1
            if s.get("pushed_at") and s["pushed_at"] > (e.pushed_at or ""):
                e.pushed_at = s["pushed_at"]
        return n

    def load_stars(self, force: bool = False):
        """stars + last update of all catalog entries in one go (GitHub search, ~10 repos per request, cached
        for a day) – otherwise “popularity” would only know the entries whose details were opened"""
        if getattr(self, "_stars_job", None) is not None or not net_allowed():
            return
        repos = [e.repo for e in self.cat.entries if e.repo]
        have = catmod.cached_stars()
        if not force and all((r or "").lower() in have for r in repos):
            return
        self._note(_("Loading stars from GitHub …"))

        def done(stars):
            self._stars_job = None
            n = self._apply_stars(stars or {})
            missing = sum(1 for r in repos if (r or "").lower() not in (stars or {}))
            self.apply_filter()
            self._note(_("Stars loaded for {n} firmwares.").format(n=n) if not missing else
                       _("Stars loaded for {n} firmwares – GitHub limit reached, the rest follows later. With your "
                         "own GitHub token (“More”) it goes faster.").format(n=n))

        def fail(msg, tb):
            self._stars_job = None
            self._note(_("GitHub: {message}").format(message=msg))
        self._stars_job = run_job(self, lambda w: catmod.fetch_stars(repos, progress=lambda a, b: w.progress.emit(a, b)),
                                  on_done=done, on_fail=fail)

    def matches(self, e: catmod.Entry) -> bool:
        m = self.menus
        if m["cat"] and not (m["cat"] & set(e.categories)):
            return False
        if m["lin"] and not (m["lin"] & set(e.lineage)):
            return False
        if m["lic"] and not ((("offen" in m["lic"]) and e.open_source) or  # i18n: ignore
                             (("geschlossen" in m["lic"]) and not e.open_source)):  # i18n: ignore
            return False
        if m["status"] and not ((("Beta" in m["status"]) and e.beta) or  # i18n: ignore
                                (("stabil" in m["status"]) and not e.beta)):  # i18n: ignore
            return False
        c = {k: b.isChecked() for k, b in self.chips.items()}
        if c["installable"] and not e.installable:
            return False
        if c["emulator"] and not e.emulator_url:
            return False
        if c["editor"] and not e.editor_url:
            return False
        if c["backup"] and e.protocol != "felucca" and not (
                fwfamily.by_key(e.key) is not None and fwfamily.by_key(e.key).backup in BACKUP_PROTOCOL_NAMES):
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
        self.count.setText(_("{shown} of {total}").format(shown=len(items), total=n))
        empty = not items
        self.view.setVisible(not empty)
        self.empty.setVisible(empty)
        if empty:
            self.empty.setText(_("No firmware matches the search and filters.\nTip: “Reset all filters” (button to the "
                                 "right of the filters) or fewer search words.") if n else
                               _("The catalog is empty – “Refresh” loads it from the internet."))

    # ------------------------------------------------------------ Thumbnails
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
            self.detail.github_loaded(key, None, _("GitHub: {message}").format(message=msg))
        run_job(self, lambda w: catmod.enrich(e.repo), on_done=done, on_fail=fail)

    # ------------------------------------------------------------ Actions
    @property
    def current(self) -> catmod.Entry | None:
        return self.detail.entry

    def install(self):
        e = self.current
        if not e:
            return
        page = self.window.page("firmware") if self.window is not None else None
        if not (e.installable or e.builtin) and e.repo:
            # no checked package in the catalog: look at the GitHub releases (same as importing a link),
            # save it as one of “my firmwares” and continue in “Install & versions”
            d = self.link_import(f"https://github.com/{e.repo}")
            if d.saved_spec is not None and page is not None and hasattr(page, "open_with"):
                self.window.go("firmware", install=d.saved_spec)
            return
        spec = e.to_spec()
        if page is not None and hasattr(page, "open_with"):
            self.window.go("firmware", install=spec)
            return
        text = (_("Installing directly from the catalog comes with the new installer.\n\nUntil then: “Add to my "
                  "firmwares” – then {name} is ready in the tab “Install & versions”, or open the author's web "
                  "installer in the browser.") if e.links.get("installer") else
                _("Installing directly from the catalog comes with the new installer.\n\nUntil then: “Add to my "
                  "firmwares” – then {name} is ready in the tab “Install & versions”.")).format(name=e.name)
        box = QMessageBox(QMessageBox.Icon.Information, _("Install"), text, parent=self)
        add = box.addButton(_("Add to my firmwares"), QMessageBox.ButtonRole.AcceptRole)
        web = box.addButton(_("Open web installer"), QMessageBox.ButtonRole.ActionRole) if e.links.get("installer") else None
        box.addButton(_("Close"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is add:
            self.add_own()
        elif web is not None and box.clickedButton() is web:
            QDesktopServices.openUrl(QUrl(e.links["installer"]))

    def _web(self, url: str, kind: str):
        e = self.current
        if not (e and url):
            return
        title = f"{e.name} – {_('Simulator') if kind == 'emulator' else _('Editor')}"
        if self.window is not None and self.window.page("simulator") is not None:
            self.window.go("simulator", url=url, title=title, kind=kind)
        else:
            QDesktopServices.openUrl(QUrl(url))
            self._note(_("{title} opened in the browser.").format(title=title))

    def emulator(self):
        self._web(self.current.emulator_url if self.current else "", "emulator")

    def editor(self):
        self._web(self.current.editor_url if self.current else "", "editor")

    def add_own(self):
        e = self.current
        if not e:
            return
        if e.builtin:
            self._note(_("{name} is already built in – you find it in the tab “Install & versions”.").format(
                           name=e.name))
            QMessageBox.information(self, _("Already there"), _("{name} is built into the app.").format(name=e.name))
            return
        from ... import linkimport as L
        d = L.empty_draft()
        L._from_entry(d, e)
        d.key = e.key if not any(s.key == e.key and s.builtin for s in firmware.catalog()) else L.unique_key(e.key)
        L.save(d, editing_key=e.key)
        self.detail.b_add.setText(_("In my firmwares ✓"))
        self._note(_("{name} is now in the tab “Install & versions”.").format(name=e.name))

    def toggle_wish(self, on: bool):
        e = self.current
        if not e:
            return
        (self.model.wish.add if on else self.model.wish.discard)(e.key)
        settings()["wishlist"] = sorted(self.model.wish)
        config.save()
        self.view.viewport().update()
        self.fill_news()

    def clear_wishlist(self) -> bool:
        """forget all remembered firmwares (the tab “Remembered” and the News that came from it)"""
        n = len(self.model.wish)
        if not n:
            notify(_("The remembered list is already empty."), "info")
            return False
        from ..common import confirm
        if not confirm(self, _("Clear the remembered list"),
                       ngettext("Forget {n} remembered firmware? Nothing is uninstalled or deleted.",
                                "Forget {n} remembered firmwares? Nothing is uninstalled or deleted.", n).format(n=n),
                       _("Clear")):
            return False
        self.model.wish = set()
        settings()["wishlist"] = []
        config.save()
        b = getattr(self.detail, "b_wish", None)
        try:
            if b is not None:
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)
        except RuntimeError:                     # the detail view was rebuilt in the meantime
            pass
        self.apply_filter()
        self.fill_news()
        notify(_("Remembered list cleared."), "success")
        return True

    # ------------------------------------------------------------ Link import
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
            self._note(_("{name} saved – now in the tab “Install & versions”.").format(name=d.saved_spec.name))
        return d

    def own_dialog(self):
        d = OwnFirmwaresDialog(self, self.cat)
        d.exec()
        if d.changed:
            self.set_catalog(self.cat)

    def token_dialog(self):
        st = settings()
        tok, ok = QInputDialog.getText(self, _("GitHub token"),
                                       _("Optional: personal GitHub token (only read access to public "
                                         "repos needed).\nWithout a token GitHub allows 60 requests per "
                                         "hour, with a token 5000.\nLeave empty to remove."),
                                       QLineEdit.EchoMode.Password, st.get("github_token", ""))
        if ok:
            st["github_token"] = tok.strip()
            config.save()

    # ------------------------------------------------------------ News
    def followed(self) -> dict[str, str]:
        """key -> reason"""
        out = {}
        for k in self.model.wish:
            out[k] = _("remembered")
        for d in config.load().extra_firmwares:
            out.setdefault(d.get("catalog_id") or d.get("key"), _("your firmware"))
        try:
            for p in self.ctx.store.profiles.values():
                if p.firmware:
                    out[p.firmware] = _("on {name}").format(name=p.name)
        except AttributeError:
            pass
        return {k: v for k, v in out.items() if k}

    def fill_news(self):
        self.news.clear()
        fol = self.followed()
        rows = []
        for c in catmod.recorded_changes():
            rows.append((c.get("date", "")[:10], c.get("key"), c.get("text", ""), _("Catalog")))
        for key, why in fol.items():
            g = self.gh.get(key)
            e = self.cat.by_key(key)
            if not (g and e):
                continue
            for r in g.get("releases", [])[:3]:
                rows.append((r["date"], key, _("{name} {version} (pre-release)").format(name=e.name, version=r["tag"])
                             if r["prerelease"] else f"{e.name} {r['tag']}", why))
        rows.sort(key=lambda r: r[0], reverse=True)
        last = time.strftime("%Y-%m-%d", time.localtime(self.last_visit)) if self.last_visit else ""
        for date, key, text, why in rows[:200]:
            new = bool(last and date and date > last)
            e = self.cat.by_key(key or "")
            it = QTreeWidgetItem([_("new") if new else "", e.name if e else key, f"{text} · {why}",
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
            text = _("No news yet. How it works: mark firmwares with “Remember” or add them to your firmwares – then "
                     "the app shows new versions here. It follows the firmwares on your FM-1 automatically.")
            if names:
                text += "\n" + _("You follow: {names}.").format(names=names)
            self.news_info.setText(text)
        else:
            self.news_info.setText(_("New versions of the firmwares you follow ({names}) and changes in the catalog. "
                                     "“new” = since your last visit. Double-click opens the firmware.").format(
                names=names or _("none")))

    def check_news(self):
        if not net_allowed():
            self.news_info.setText(_("Offline – news will come next time."))
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
                                                                   self.news_info.setText(
                                                                       _("GitHub: {message}").format(message=m))))

    # ------------------------------------------------------------ Tools & knowledge
    def fill_resources(self):
        self.res.clear()
        words = self.res_search.text().lower().split()
        groups: dict[str, QTreeWidgetItem] = {}
        # section titles of awesome-fm-1 (English upstream) – shown translated
        names = {k: _(k) for k in (
            N_("Editors, librarians and companion software"), N_("Firmware update and recovery"),
            N_("Patches and sound banks"), N_("Hardware add-ons"), N_("Documentation and guides"),
            N_("Articles and reviews"), N_("Videos"), N_("Communities"),
            N_("Reverse engineering and firmware development"), N_("Related M-VAVE projects"))}
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


PAGE = PageSpec(key="discover", title=_("Firmware+"), group="daten", factory=_make,
                icons=("system-software-update", "plasmadiscover", "system-software-install", "applications-all"),
                order=12, description=_("All FM-1 firmwares in one place: find, try out, install, switch versions, "
                                        "back to the original"),
                keywords=("firmware", "firmwares", "entdecken", "katalog", "store", "installieren", "update",
                          "version", "emulator", "link", "awesome-fm-1", "mooring", "hub",
                          "discover", "catalog", "install", "switch", "original"))
