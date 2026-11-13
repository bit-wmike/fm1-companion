# SPDX-License-Identifier: GPL-3.0-only
"""Settings."""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                             QSpinBox, QTabWidget, QVBoxLayout, QWidget)
from PyQt6.QtCore import Qt

from .. import config, firmware, i18n
from ..i18n import N_, _
from .common import confirm, german_buttons, icon, show_error


def _defaults(module: str, name: str):
    import importlib
    return lambda: dict(getattr(importlib.import_module(module, __package__), name))


# Sections that keep their own settings (config.module): key, name (msgid), defaults, keys that stay.
# Kept: what is yours rather than a setting – a token, the wishlist, MIDI learn assignments, routing presets,
# chosen sound fonts, trusted simulator origins, the mirror folder, the Studio's last device.
PAGE_SETTINGS = [
    ("catalog", N_("Catalog (filters, sorting, preview images, automatic update)"),
     _defaults("..ui.pages.discover", "DEFAULTS"), ("wishlist", "github_token", "last_visit")),
    ("daten", N_("Automatic backups (on connect, periodically, mirror options)"),
     _defaults("..autobackup", "DEFAULTS"), ("mirror",)),
    ("studio", N_("Studio (inputs, export options)"), _defaults(".studio_service", "SETTINGS"), ("last_profile",)),
    ("recorder", N_("Recording (sync, bars, split, export format)"), _defaults(".recorder_page", "SETTINGS"), ()),
    ("midi", N_("MIDI (keyboard, clock, filters)"), _defaults("..ui.pages.midi", "SETTINGS"),
     ("learn", "learn_port", "routing_presets")),
    ("musik", N_("View & Export (lanes, drum channel, loops)"), _defaults("..ui.pages.project_view", "SETTINGS"), ()),
    ("simulator", N_("Simulator (latency, inputs)"), _defaults("..ui.pages.simulator", "SETTINGS"),
     ("device_origins",)),
    ("soundmodule", N_("Sound module (driver, latency, gain, channels)"),
     _defaults("..ui.pages.soundmodule", "SETTINGS"), ("soundfont", "custom")),
    ("audio", N_("Audio output"), lambda: {"output": "", "keep_off_fm1": True}, ()),
    ("rettung", N_("Rescue (expert mode)"), lambda: {"experte": False}, ()),
    ("installer", N_("Installation (remembered answers)"), lambda: {"v15_choice": {}}, ()),
    ("storage", N_("Automatic cleanup (Storage tab)"), _defaults("..storage", "DEFAULTS"), ("last_auto",)),
]


def reset_page_settings(keys) -> list[str]:
    """put the chosen sections back to their defaults (what is yours stays, see PAGE_SETTINGS) and save.
    -> the keys that were reset"""
    done = []
    for key, _title, defaults, keep in PAGE_SETTINGS:
        if key in keys:
            config.reset_module(key, defaults(), keep)
            done.append(key)
    if done:
        config.save()
    return done


class SettingsDialog(QDialog):
    def __init__(self, ctx, parent=None, tab: str = ""):
        super().__init__(parent)
        self.ctx = ctx
        s = ctx.settings
        self.setWindowTitle(_("Settings"))
        self.setMinimumSize(620, 460)
        lay = QVBoxLayout(self)
        self.tabs = tabs = QTabWidget()
        lay.addWidget(tabs)

        # General
        g = QWidget()
        f = QFormLayout(g)
        self.channel = QComboBox()
        self.channel.addItem(_("stable versions (recommended)"), "stable")
        self.channel.addItem(_("also release candidates (pre-releases, newest features)"), "rc")
        self.channel.setToolTip(_("Stable: finished versions only. Release candidates (RC): pre-releases with the "
                                  "newest features – well tested, but not final yet."))
        self.channel.setCurrentIndex(max(0, self.channel.findData(s.channel)))
        self.check = QCheckBox(_("look for firmware updates when an FM-1 is plugged in (asks GitHub)"))
        self.check.setToolTip(_("Fetches the version list of the FM-1 firmware from GitHub. When switched off, the "
                                "app only goes online for this when you click."))
        self.check.setChecked(s.check_on_start)
        self.autobk = QCheckBox(_("back up automatically before every update"))
        self.autobk.setChecked(s.auto_backup_before_update)
        self.keep = QSpinBox()
        self.keep.setRange(0, 500)
        self.keep.setSpecialValueText(_("keep all"))
        self.keep.setValue(s.keep_backups)
        self.keep.setToolTip(_("How many automatic backups are kept per device. Manual backups always stay."))
        f.addRow(_("Update channel:"), self.channel)
        f.addRow("", self.check)
        f.addRow("", self.autobk)
        f.addRow(_("Keep automatic backups:"), self.keep)
        self.careful = QComboBox()
        self.careful.addItem(_("automatic (when the FM-1 is connected through a USB hub)"), "auto")
        self.careful.addItem(_("always"), "always")
        self.careful.addItem(_("never"), "never")
        self.careful.setToolTip(_("Careful USB mode waits longer for the FM-1 to restart, answers its requests more "
                                  "slowly and resumes the write automatically if the USB connection drops – "
                                  "the usual trouble with USB hubs. All safety checks stay the same."))
        self.careful.setCurrentIndex(max(0, self.careful.findData(getattr(s, "careful_usb", "auto"))))
        f.addRow(_("Careful USB mode for flashing:"), self.careful)
        # language of the user interface: applied at the next start (texts are translated when pages are built)
        self.language = QComboBox()
        self.language.addItem(_("System language ({name})").format(
            name=i18n.language_name(i18n.system_language())), i18n.SYSTEM)
        for code in i18n.available_languages():
            self.language.addItem(i18n.language_name(code), code)
        self.language.setCurrentIndex(max(0, self.language.findData(getattr(s, "language", i18n.SYSTEM))))
        self.language.setToolTip(_("Language of the user interface. Takes effect after restarting the app."))
        self.language_hint = QLabel(_("Takes effect after restarting the app."))
        self.language_hint.setProperty("role", "small")
        f.addRow(_("Language:"), self.language)
        f.addRow("", self.language_hint)
        reset = QPushButton(icon("edit-undo", "document-revert"), _("Default values"))
        reset.setToolTip(_("Puts the options in this window back to their default values (also appearance and "
                           "recording). Folders, devices and your firmware list stay. Only saved with OK."))
        reset.clicked.connect(self.restore_defaults)
        f.addRow("", reset)
        b_pages = QPushButton(icon("edit-clear-history", "edit-undo"), _("Reset settings of …"))
        b_pages.setToolTip(_("Puts the settings the pages keep themselves (catalog, Studio, MIDI, recording …) back "
                             "to their default values – chosen one by one, applied at once."))
        b_pages.clicked.connect(lambda: self.reset_pages_dialog())
        f.addRow("", b_pages)
        tabs.addTab(g, _("General"))

        # Appearance
        from . import theme as th
        pr = th.prefs()
        ap = QWidget()
        af0 = QFormLayout(ap)
        self.theme = QComboBox()
        for k, t in th.MODES:
            self.theme.addItem(t, k)
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.theme)))
        self.accent = QComboBox()
        for name, col in th.ACCENTS:
            self.accent.addItem(self._swatch(col), name, col)
        if self.accent.findData(pr.get("accent")) < 0:
            self.accent.addItem(self._swatch(pr.get("accent")), _("Custom color"), pr.get("accent"))
        self.accent.addItem(icon("color-management"), _("Choose custom color …"), "")
        self.accent.setCurrentIndex(max(0, self.accent.findData(pr.get("accent"))))
        self.accent.activated.connect(self._accent_pick)
        self.icons = QComboBox()
        self.icons.addItem(_("App design (uniform)"), "app")
        self.icons.addItem(_("System theme (Breeze/Papirus)"), "system")
        self.icons.setCurrentIndex(max(0, self.icons.findData(pr.get("icons", "app"))))
        self.zoom = QSpinBox()
        self.zoom.setRange(80, 160)
        self.zoom.setSingleStep(10)
        self.zoom.setSuffix(" %")
        self.zoom.setValue(round(float(pr.get("zoom", 1.0)) * 100))
        self.notify = QComboBox()
        for k, t in (("background", _("when the window is in the background")), ("always", _("always")), ("never", _("never"))):
            self.notify.addItem(t, k)
        self.notify.setCurrentIndex(max(0, self.notify.findData(pr.get("desktop_notify", "background"))))
        self.splash = QCheckBox(_("Show splash screen"))
        self.splash.setChecked(bool(pr.get("splash", True)))
        af0.addRow(_("Theme:"), self.theme)
        af0.addRow(_("Accent color:"), self.accent)
        af0.addRow(_("Icons:"), self.icons)
        self.icons.setToolTip(_("Icons already shown change completely after restarting the app."))
        af0.addRow(_("Interface size:"), self.zoom)
        af0.addRow(_("Desktop notifications:"), self.notify)
        af0.addRow("", self.splash)
        self.splash.setToolTip(_("Applies from the next start."))
        wiz = QPushButton(icon("tools-wizard"), _("Run setup assistant again …"))
        wiz.clicked.connect(self._wizard)
        af0.addRow("", wiz)
        tabs.addTab(ap, _("Appearance"))

        # Folders
        o = QWidget()
        ol = QVBoxLayout(o)
        of = QFormLayout()
        self.root = QLineEdit(s.user_root)
        b = QPushButton(icon("folder-open"), "")
        b.clicked.connect(lambda: self._pick(self.root))
        h = QHBoxLayout()
        h.addWidget(self.root)
        h.addWidget(b)
        of.addRow(_("Main folder:"), h)
        ol.addLayout(of)
        hint = QLabel(_("It holds backups, projects, kits, recordings, firmware and the sample folder “Samples”.")
                      + " " + _("Choosing another folder does not move existing files – move them yourself "
                                "(with the app closed)."))
        hint.setWordWrap(True)
        ol.addWidget(hint)
        ol.addWidget(QLabel(_("More sample folders (e.g. your Pocket Operator samples):")))
        self.roots = QListWidget()
        for r in s.sample_roots:
            self.roots.addItem(r)
        ol.addWidget(self.roots, 1)
        b_add = QPushButton(icon("list-add"), _("Add …"))
        b_add.clicked.connect(self._add_root)
        b_rm = QPushButton(icon("list-remove"), _("Remove"))
        b_rm.clicked.connect(lambda: self.roots.takeItem(self.roots.currentRow()))
        h2 = QHBoxLayout()
        h2.addWidget(b_add)
        h2.addWidget(b_rm)
        h2.addStretch(1)
        ol.addLayout(h2)
        self.norm = QCheckBox(_("normalize new kit sounds"))
        self.norm.setChecked(s.sample_default_normalize)
        self.trim = QCheckBox(_("trim silence at start/end"))
        self.trim.setChecked(s.sample_default_trim)
        ol.addWidget(self.norm)
        ol.addWidget(self.trim)
        tabs.addTab(o, _("Folders && Samples"))

        # Recording
        a = QWidget()
        af = QFormLayout(a)
        self.aud = QLineEdit(s.audacity_cmd)
        self.open_aud = QCheckBox(_("open in Audacity after recording"))
        self.open_aud.setChecked(s.record_open_audacity)
        af.addRow(_("Audacity command:"), self.aud)
        af.addRow("", self.open_aud)
        tabs.addTab(a, _("Recording"))

        # Devices
        d = QWidget()
        dl = QVBoxLayout(d)
        intro = QLabel(_("Known FM-1. The app enters the firmware version itself when installing; for firmware "
                         "installed before, you can set it here."))
        intro.setWordWrap(True)                 # previously cut off at a 620 px dialog width
        dl.addWidget(intro)
        self.devs = QListWidget()
        dl.addWidget(self.devs, 1)
        b_ren = QPushButton(icon("edit-rename"), _("Rename"))
        b_ren.clicked.connect(self._dev_rename)
        b_ver = QPushButton(icon("document-edit"), _("Set firmware version"))
        b_ver.clicked.connect(self._dev_version)
        b_del = QPushButton(icon("edit-delete"), _("Forget"))
        b_del.clicked.connect(self._dev_delete)
        h3 = QHBoxLayout()
        for bb in (b_ren, b_ver, b_del):
            h3.addWidget(bb)
        h3.addStretch(1)
        dl.addLayout(h3)
        tabs.addTab(d, _("Devices"))
        self._fill_devs()

        # Storage: sizes, cleanup (measured when the tab is first shown)
        from .storage_tab import StorageTab
        self.storage = StorageTab(ctx, self)
        self.storage_index = tabs.addTab(self.storage, _("Storage"))
        tabs.currentChanged.connect(self._tab_changed)
        if tab == "storage":
            tabs.setCurrentIndex(self.storage_index)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        german_buttons(bb)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _tab_changed(self, i):
        if i == self.storage_index and not self.storage.cats:
            self.storage.refresh()

    def restore_defaults(self):
        """default values into the widgets (General, Appearance, Recording, sample options); saved only with OK"""
        from . import theme as th
        d = config.Settings()
        self.channel.setCurrentIndex(max(0, self.channel.findData(d.channel)))
        self.check.setChecked(d.check_on_start)
        self.autobk.setChecked(d.auto_backup_before_update)
        self.keep.setValue(d.keep_backups)
        self.careful.setCurrentIndex(max(0, self.careful.findData(d.careful_usb)))
        self.language.setCurrentIndex(max(0, self.language.findData(i18n.SYSTEM)))
        self.theme.setCurrentIndex(max(0, self.theme.findData(d.theme)))
        if self.accent.findData(th.DEFAULT_ACCENT) >= 0:
            self.accent.setCurrentIndex(self.accent.findData(th.DEFAULT_ACCENT))
        self.icons.setCurrentIndex(max(0, self.icons.findData("app")))
        self.zoom.setValue(100)
        self.notify.setCurrentIndex(max(0, self.notify.findData("background")))
        self.splash.setChecked(True)
        self.norm.setChecked(d.sample_default_normalize)
        self.trim.setChecked(d.sample_default_trim)
        self.aud.setText(d.audacity_cmd)
        self.open_aud.setChecked(d.record_open_audacity)

    def reset_pages_dialog(self, preselect=()) -> list[str]:
        """choose the sections whose own settings go back to the defaults (applied right away, after a question)"""
        dlg = QDialog(self)
        dlg.setWindowTitle(_("Reset settings of …"))
        v = QVBoxLayout(dlg)
        intro = QLabel(_("Tick the pages whose settings go back to their default values. What is yours stays: "
                         "folders, devices, backups, your firmware list, the GitHub token, the catalog's remembered "
                         "list, MIDI learn assignments and routing presets, chosen sound fonts."))
        intro.setWordWrap(True)
        v.addWidget(intro)
        lst = QListWidget()
        for key, title, _d, _k in PAGE_SETTINGS:
            it = QListWidgetItem(_(title))
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if key in preselect else Qt.CheckState.Unchecked)
            lst.addItem(it)
        v.addWidget(lst, 1)
        note = QLabel(_("Pages that are already open may show the old values until the app is restarted."))
        note.setWordWrap(True)
        note.setProperty("role", "small")
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = bb.addButton(_("Reset"), QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setProperty("variant", "danger")
        german_buttons(bb)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        self._reset_list = lst                 # (tests)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return []
        keys = [lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.count())
                if lst.item(i).checkState() == Qt.CheckState.Checked]
        if not keys:
            return []
        names = [_(t) for k, t, _d, _k in PAGE_SETTINGS if k in keys]
        if not confirm(self, _("Reset settings of …"),
                       _("Put these settings back to their default values?<br>{list}").format(
                           list="<br>".join("• " + n for n in names)), _("Reset"), danger=True):
            return []
        done = reset_page_settings(keys)
        if "storage" in done:
            self.storage.load_values()
        w = self.parent()
        if done and w is not None and hasattr(w, "notify"):
            w.notify(_("Settings reset: {list}.").format(list=", ".join(names)), "success")
        return done

    @staticmethod
    def _swatch(col: str):
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(col))
        p.setPen(QColor(0, 0, 0, 60))
        p.drawEllipse(1, 1, 14, 14)
        p.end()
        return QIcon(pm)

    def _accent_pick(self, i):
        if self.accent.itemData(i):
            return
        from PyQt6.QtGui import QColor
        from PyQt6.QtWidgets import QColorDialog
        col = QColorDialog.getColor(QColor(self.accent.itemData(0) or "#ff7a1a"), self, _("Accent color"))
        if col.isValid():
            self.accent.insertItem(i, self._swatch(col.name()), _("Custom color"), col.name())
            self.accent.setCurrentIndex(i)
        else:                                   # cancelled: keep the previous accent color
            from . import theme as th
            self.accent.setCurrentIndex(max(0, self.accent.findData(th.prefs().get("accent"))))

    def _wizard(self):
        w = self.parent()
        self.reject()
        if w is not None and hasattr(w, "open_wizard"):
            w.open_wizard()

    def _pick(self, edit):
        dd = QFileDialog.getExistingDirectory(self, _("Choose folder"), edit.text())
        if dd:
            edit.setText(dd)

    def _add_root(self):
        dd = QFileDialog.getExistingDirectory(self, _("Sample folder"))
        if dd:
            self.roots.addItem(dd)

    def _fill_devs(self):
        self.devs.clear()
        for p in sorted(self.ctx.store.profiles.values(), key=lambda x: x.name):
            fw = firmware.spec(p.firmware)
            what = f"{fw.name if fw else p.firmware} {p.firmware_version}".strip() or _("firmware unknown")
            it = QListWidgetItem(f"{p.name} – {what}" + (_("  · Serial no. {serial}").format(serial=p.serial) if p.serial else ""))
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            self.devs.addItem(it)

    def _cur(self):
        it = self.devs.currentItem()
        return self.ctx.store.profiles.get(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def _dev_rename(self):
        p = self._cur()
        if p:
            name, ok = QInputDialog.getText(self, _("Rename"), _("Name:"), text=p.name)
            if ok and name.strip():
                p.name = name.strip()
                self.ctx.store.save()
                self._fill_devs()

    def _dev_version(self):
        p = self._cur()
        if not p:
            return
        keys = [s.key for s in firmware.catalog()]
        key, ok = QInputDialog.getItem(self, _("Firmware"), _("Firmware:"), keys, max(0, keys.index(p.firmware)) if p.firmware in keys else 0, False)
        if not ok:
            return
        tag, ok = QInputDialog.getText(self, _("Version"), _("Version (as in the release, e.g. v1.6.0-rc.7):"),
                                       text=p.firmware_version)
        if ok:
            p.firmware, p.firmware_version = key, tag.strip()
            self.ctx.store.save()
            self._fill_devs()
            self.ctx.changed.emit()

    def _dev_delete(self):
        """forget a device – optionally with its Studio state (backups and projects always stay)"""
        p = self._cur()
        if not p:
            return False
        from .. import storage
        from .. import studio as S
        folder = p.folder_name
        has_state = S.state_path(folder).parent.is_dir()
        service = getattr(getattr(self.ctx, "window", None), "studio", None)
        in_use = has_state and storage.studio_state_in_use(folder, service)
        box = QMessageBox(QMessageBox.Icon.Question, _("Forget device"),
                          _("Forget {name}? Backups and projects are kept.").format(name=p.name), parent=self)
        chk = None
        if has_state:
            chk = QCheckBox(_("also delete its Studio state (your work in the Studio for this device)"))
            if in_use:
                chk.setEnabled(False)
                chk.setToolTip(_("The Studio is working on this state right now – open another state there first."))
            box.setCheckBox(chk)
        yes = box.addButton(_("Forget"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(yes)
        self._forget_box = box                 # (tests)
        box.exec()
        if box.clickedButton() is not yes:
            return False
        self.ctx.store.delete(p.id)
        if chk is not None and chk.isChecked() and chk.isEnabled():
            try:
                storage.delete_studio_state(folder)
            except OSError as e:
                show_error(self, _("Not deleted"), _("The device was forgotten, but its Studio state could not be "
                                                     "deleted: {error}").format(error=e.strerror or e))
        self._fill_devs()
        return True

    def _ok(self):
        s = self.ctx.settings
        s.channel = self.channel.currentData()
        s.check_on_start = self.check.isChecked()
        s.auto_backup_before_update = self.autobk.isChecked()
        s.keep_backups = self.keep.value()
        s.careful_usb = self.careful.currentData() or "auto"
        s.theme = self.theme.currentData()
        from . import theme as th
        pr = th.prefs()
        pr["accent"] = self.accent.currentData() or pr.get("accent")
        pr["icons"] = self.icons.currentData()
        pr["zoom"] = self.zoom.value() / 100
        pr["desktop_notify"] = self.notify.currentData()
        pr["splash"] = self.splash.isChecked()
        s.user_root = self.root.text().strip() or s.user_root
        s.sample_roots = [self.roots.item(i).text() for i in range(self.roots.count())]
        s.sample_default_normalize = self.norm.isChecked()
        s.sample_default_trim = self.trim.isChecked()
        s.audacity_cmd = self.aud.text().strip() or "audacity"
        s.record_open_audacity = self.open_aud.isChecked()
        self.storage.apply()
        lang = self.language.currentData() or i18n.SYSTEM
        self.language_changed = lang != getattr(s, "language", i18n.SYSTEM)
        s.language = lang
        if not config.save(s):              # not writable: say that it only applies until exit
            show_error(self, _("Settings not saved"), config.save_error)
        config.ensure_dirs()
        if self.language_changed:
            w = self.parent()
            if w is not None and hasattr(w, "notify"):
                w.notify(_("The new language is used after restarting the app."), "info")
        self.accept()
