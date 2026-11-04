# SPDX-License-Identifier: GPL-3.0-only
"""Setup wizard (first start, re-run any time via Help) and “What's new in 2.0”."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
                             QWizard, QWizardPage)

from .. import SUITE_NAME, __version__, config, diag
from . import theme
from .common import heading, icon
from ..i18n import N_, _, pgettext


def _p(text: str, role: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.TextFormat.RichText)
    lab.setOpenExternalLinks(True)
    if role:
        lab.setProperty("role", role)
    return lab


class _Welcome(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle(_("Welcome to the {suite}").format(suite=SUITE_NAME))
        self.setSubTitle(_("Everything is set up in a minute. You can open the wizard again later via "
                           "Help › Setup wizard."))
        lay = QVBoxLayout(self)
        lay.addWidget(_p(_("The suite takes care of everything around your M-VAVE FM-1: changing "
                           "firmware, backups, sounds and presets, samples, MIDI, simulator and "
                           "recording.")))
        row = QHBoxLayout()
        row.addWidget(QLabel(_("Appearance:")))
        self.theme = QComboBox()
        for k, t in theme.MODES:
            self.theme.addItem(t, k)
        cur = wz.ctx.settings.theme
        self.theme.setCurrentIndex(max(0, self.theme.findData("auto" if cur == "system" and not wz.upgrade else cur)))
        self.theme.currentIndexChanged.connect(self._preview)
        row.addWidget(self.theme, 1)
        lay.addLayout(row)
        lay.addStretch(1)
        self.wz = wz

    def _preview(self):
        from PyQt6.QtWidgets import QApplication
        theme.apply(QApplication.instance(), self.theme.currentData())
        self.wz._previewed = True


class _Folder(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle(_("Main folder"))
        self.setSubTitle(_("Your backups, projects, kits, recordings and firmware downloads live here."))
        lay = QVBoxLayout(self)
        h = QHBoxLayout()
        self.edit = QLineEdit(wz.ctx.settings.user_root)
        b = QPushButton(icon("folder-open"), _("Choose …"))
        b.clicked.connect(self._pick)
        h.addWidget(self.edit, 1)
        h.addWidget(b)
        lay.addLayout(h)
        lay.addWidget(_p(_("Tip: put the folder where you also back it up (e.g. on the NAS drive). The "
                           "app never deletes it – not even when uninstalling."), "muted"))
        lay.addStretch(1)
        # NOT as mandatory field "root*": Qt only counts a mandatory field as filled once it differs from its
        # initial value – with a prefilled ~/FM-1 “Next” stayed grayed out and the wizard got stuck.
        self.registerField("root", self.edit)
        self.edit.textChanged.connect(self.completeChanged)

    def isComplete(self) -> bool:
        return bool(self.edit.text().strip())

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, _("Choose main folder"), self.edit.text())
        if d:
            self.edit.setText(d)

    def validatePage(self) -> bool:
        """The folder must be creatable and writable – otherwise the wizard stays here and says why."""
        import os
        root = Path(self.edit.text().strip() or "~").expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".fm1c-schreibtest"
            probe.write_text("ok")
            probe.unlink()
        except OSError as e:
            from .common import show_error
            from .device import folder_error_text
            show_error(self, _("Main folder not writable"), folder_error_text(root, e))
            return False
        self.edit.setText(str(root))
        return True


class _Samples(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle(_("Sample folders"))
        self.setSubTitle(_("Where are your samples (e.g. Pocket Operator sounds)? Optional – can also be "
                           "done later."))
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        for r in wz.ctx.settings.sample_roots:
            self.list.addItem(r)
        lay.addWidget(self.list, 1)
        h = QHBoxLayout()
        add = QPushButton(icon("list-add"), _("Add folder …"))
        add.clicked.connect(self._add)
        rm = QPushButton(icon("list-remove"), _("Remove"))
        rm.clicked.connect(lambda: self.list.takeItem(self.list.currentRow()))
        h.addWidget(add)
        h.addWidget(rm)
        h.addStretch(1)
        lay.addLayout(h)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, _("Sample folder"))
        if d:
            self.list.addItem(d)

    def roots(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]


class _Deps(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle(_("Check dependencies"))
        self.setSubTitle(_("Which system packages the suite needs and whether they are installed."))
        lay = QVBoxLayout(self)
        self.text = _p("")
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.text)
        self.cmd_label = QLabel("")                # the apt command: wrapping, copyable, monospace
        self.cmd_label.setWordWrap(True)
        self.cmd_label.setTextFormat(Qt.TextFormat.PlainText)
        self.cmd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.cmd_label.setStyleSheet("QLabel { font-family: monospace; padding: 6px; border: 1px solid palette(mid); "
                                     "border-radius: 6px; }")
        self.cmd_label.hide()
        lay.addWidget(self.cmd_label)
        h = QHBoxLayout()
        self.copy = QPushButton(icon("edit-copy"), _("Copy command"))
        self.copy.setToolTip(_("Put the apt command on the clipboard – then paste it in the terminal"))
        self.copy.clicked.connect(self._copy)
        again = QPushButton(icon("view-refresh"), _("Check again"))
        again.clicked.connect(self.initializePage)
        h.addWidget(self.copy)
        h.addWidget(again)
        h.addStretch(1)
        lay.addLayout(h)
        lay.addStretch(1)
        self.cmd = ""

    def initializePage(self):
        states = diag.check_deps()
        def state(s) -> str:
            if s.ok:
                return s.why
            return (_("{why} – <b>missing</b>") if s.required else _("{why} – optional")).format(why=s.why)
        rows = "".join(f"<tr><td>{'✔' if s.ok else ('✖' if s.required else '–')}</td><td>{s.package}</td>"
                       f"<td>{state(s)}</td></tr>" for s in states)
        self.cmd = diag.apt_command(states)
        tail = ("<p>" + _("Install what is missing (in the terminal):") + "</p>") if self.cmd else \
            ("<p><b>" + _("Everything is there.") + "</b></p>")
        self.text.setText(f"<table cellpadding='2'>{rows}</table>{tail}")
        self.cmd_label.setText(self.cmd)
        self.cmd_label.setVisible(bool(self.cmd))
        self.copy.setEnabled(bool(self.cmd))

    def _copy(self):
        QGuiApplication.clipboard().setText(self.cmd)
        self.copy.setText(_("Copied ✔"))
        QTimer.singleShot(2000, lambda: self.copy.setText(_("Copy command")))


class _Device(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.wz = wz
        self.setTitle(_("Connect & name the FM-1"))
        self.setSubTitle(_("Switch your FM-1 on and connect it with a USB data cable. It appears here by "
                           "itself."))
        lay = QVBoxLayout(self)
        self.state = _p(_("Searching …"))
        lay.addWidget(self.state)
        h = QHBoxLayout()
        h.addWidget(QLabel(_("Name:")))
        self.name = QLineEdit()
        self.name.setPlaceholderText(_("e.g. Studio FM-1"))
        h.addWidget(self.name, 1)
        lay.addLayout(h)
        lay.addWidget(_p(_("A second FM-1? Just plug it in instead – every device gets its own profile, "
                           "own colour and own backups. This step is optional."), "muted"))
        lay.addStretch(1)
        wz.ctx.changed.connect(self._update)
        wz.ctx.ports_changed.connect(self._update)
        self._named_for = None

    def initializePage(self):
        self._update()

    def _update(self):
        c = self.wz.ctx
        proto = getattr(c, "protocol", "felucca" if c.connected else "")
        if c.profile and (c.connected or proto == "generic"):
            if c.info:
                fw = c.info.version
            else:
                from .device import firmware_name
                fw = (firmware_name(c.firmware_key) or _("Firmware without editor protocol")) + \
                     (f" ({c.identity})" if c.identity else "")
            self.state.setText(f"<b style='color:{c.profile.color}'>●</b> "
                               + _("<b>Found:</b> {port} – {firmware}").format(port=c.port.label, firmware=fw))
            if self._named_for != c.profile.id:
                self._named_for = c.profile.id
                self.name.setText(c.profile.name)
            self.name.setEnabled(True)
        elif c.port and proto == "update":
            self.state.setText(_("An FM-1 is waiting in <b>update mode</b>. After the wizard, continue on "
                                 "the page “Recovery” – there the interrupted installation is completed."))
            self.name.setEnabled(c.profile is not None)
        elif c.port:
            self.state.setText(_("FM-1 found, but it does not answer (yet) – one moment. If it does not "
                                 "answer at all: check the data cable, close other MIDI programs."))
            self.name.setEnabled(c.profile is not None)
        else:
            self.state.setText(_("No FM-1 connected yet. You can also skip this step."))
            self.name.setEnabled(False)

    def apply(self):
        c = self.wz.ctx
        n = self.name.text().strip()
        if c.profile and n and n != c.profile.name:
            try:
                c.rename_profile(n)
            except OSError as e:               # backup folder not movable: name stays, notice instead of crash
                from .common import show_error
                show_error(self, _("Renaming not possible"), str(e))


class _AutoBackup(QWizardPage):
    """Info step: what the app does by itself on the first connection."""

    def __init__(self, wz):
        super().__init__()
        self.setTitle(pgettext("wizard page", "Automatic backup"))
        self.setSubTitle(_("On the first connection the app makes a backup automatically."))
        lay = QVBoxLayout(self)
        lay.addWidget(_p(_("As soon as an FM-1 with Melodee, Skiff or SLOOP is connected, the app backs "
                           "up the complete state once a day when plugging in (running music, all "
                           "projects, user presets, settings) to <b>FM-1/Backups/&lt;device name&gt;</b>. "
                           "This takes a few seconds; the FM-1 briefly stops playback for "
                           "it.<br><br>Before every firmware installation and before every restore an "
                           "additional safety backup is made. Automatic backups are tidied up by rules "
                           "(the latest ones, the newest per month and per firmware stay); the app never "
                           "deletes your own backups.<br><br>You can set this under <b>Backups</b> and in "
                           "the settings.")))
        lay.addStretch(1)


class _Channel(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle(_("Update channel"))
        self.setSubTitle(_("Which firmware versions should the app offer?"))
        lay = QVBoxLayout(self)
        self.grp = QButtonGroup(self)
        self.stable = QRadioButton(_("Stable versions (recommended)"))
        self.rc = QRadioButton(_("Release candidates too"))
        for b in (self.stable, self.rc):
            self.grp.addButton(b)
            lay.addWidget(b)
        (self.rc if wz.ctx.settings.channel == "rc" else self.stable).setChecked(True)
        lay.addWidget(_p(_("Release candidates (RC) are pre-releases with the newest features – usually "
                           "well tested, but not final yet. You can change this at any time in the "
                           "settings."), "muted"))
        lay.addWidget(_p(_("Before every update the app makes a backup automatically."), "muted"))
        lay.addStretch(1)


class _Done(QWizardPage):
    def __init__(self, wz):
        super().__init__()
        self.setTitle(_("Done!"))
        self.setSubTitle(_("Have fun with your FM-1."))
        lay = QVBoxLayout(self)
        lay.addWidget(_p(_("<ul><li><b>Ctrl+K</b> – find everything</li><li><b>Ctrl+B</b> – "
                           "backup</li><li><b>F1</b> – help</li></ul>At the bottom right you see "
                           "compactly how your computer is doing – a click opens the system check.")))
        self.tour = QPushButton(icon("help-hint"), _("What's new in 2.0?"))
        self.tour.clicked.connect(lambda: QTimer.singleShot(0, wz.window.open_whats_new) if wz.window else None)
        lay.addWidget(self.tour, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)


class FirstRunWizard(QWizard):
    def __init__(self, ctx, window=None, upgrade: bool = False):
        super().__init__(window)
        self.ctx, self.window, self.upgrade = ctx, window, upgrade
        self.setWindowTitle(_("Setup"))
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        from .main_window import app_icon
        self.setPixmap(QWizard.WizardPixmap.LogoPixmap, app_icon().pixmap(48, 48))
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        for b, t in ((QWizard.WizardButton.NextButton, _("Next >")), (QWizard.WizardButton.BackButton, _("< "
                                                                                                        "Back")),
                     (QWizard.WizardButton.FinishButton, _("Finish")), (QWizard.WizardButton.CancelButton, _("Cancel"))):
            self.setButtonText(b, t)
        self.resize(680, 500)
        self.p_welcome, self.p_folder, self.p_samples = _Welcome(self), _Folder(self), _Samples(self)
        self.p_deps, self.p_device, self.p_channel, self.p_done = _Deps(self), _Device(self), _Channel(self), _Done(self)
        self.p_autobackup = _AutoBackup(self)
        for p in (self.p_welcome, self.p_folder, self.p_samples, self.p_deps, self.p_device, self.p_autobackup,
                  self.p_channel, self.p_done):
            self.addPage(p)
        self._mode_before = ctx.settings.theme

    def accept(self):
        s = self.ctx.settings
        s.theme = self.p_welcome.theme.currentData()
        s.user_root = self.p_folder.edit.text().strip() or s.user_root
        s.sample_roots = self.p_samples.roots()
        s.channel = "rc" if self.p_channel.rc.isChecked() else "stable"
        self.p_device.apply()
        pr = theme.prefs()
        pr["wizard_done"] = True
        config.save(s)
        config.ensure_dirs()
        super().accept()

    def reject(self):
        # only restore if the preview actually changed the theme (needlessly restyling the whole
        # app on cancel crashed offscreen on the second opening)
        if getattr(self, "_previewed", False):
            from PyQt6.QtWidgets import QApplication
            theme.apply(QApplication.instance(), self._mode_before)
            self._previewed = False
        super().reject()


# ---------------------------------------------------------------- What's new ---
# (title, text, icon, possible page keys/module names)
NEWS = [
    (N_("Studio"), N_("The digital twin of your FM-1: the same firmware on the computer – play, export as WAV "
                  "(also single tracks) for Logic/Audacity and write back to the FM-1."), "audio-lines", ("studio",)),
    (N_("Firmware+"), N_("All firmwares in one place: catalog with everything there is for the FM-1, plus "
                     "install, switch versions and back to the original."), "compass", ("discover", "catalog")),
    (N_("Recovery"), N_("Step by step back from update mode – even if the display stays dark."), "life-buoy",
     ("recovery",)),
    (N_("Simulator"), N_("Play FM-1 firmwares directly on the computer, without a device."), "monitor-play", ("simulator",)),
    (N_("Presets"), N_("Browse, back up, swap and restore all user presets."), "library", ("presets",)),
    (N_("FM Voices"), N_("Import DX7 sounds and bring them to the FM-1 as FM voices."), "audio-lines", ("voices",)),
    (N_("MIDI"), N_("Connect ports, monitor, send clock."), "piano", ("midi",)),
    (N_("Sound Module"), N_("Make the MIDI tracks 5–8 audible – with FluidSynth on the computer."), "speaker", ("soundmodule",)),
    (N_("Projects › View & export"), N_("View projects as tracks and patterns and export them as MIDI."), "list-music",
     ("project_view", "projectview")),
    (N_("Compose"), N_("Generate ideas, chords and patterns and play them on the device."), "pen-tool", ("compose",)),
    (N_("Backups › Browse"), N_("Look into backups, compare, extract parts."), "archive",
     ("backup_browser", "backupbrowser")),
    (N_("Devices"), N_("All your FM-1 with profile, colour and history."), "keyboard-music", ("devices",)),
    (N_("Help Center"), N_("Guides, FAQ and diagnostics – also offline. F1."), "book-open", ("help",)),
    (N_("Command search"), N_("Ctrl+K: every page, every action, every help topic via the keyboard."), "command", ("@palette",)),
    (N_("System check"), N_("Finds loose contacts, under-voltage and USB audio errors in the kernel log."), "activity",
     ("@syscheck",)),
    (N_("New design"), N_("Light, dark or automatic – with accent colour and consistent icons."), "palette",
     ("@settings",)),
]


def find_page(window, candidates) -> str | None:
    """Find the page key: directly or via the page's module name (ui/pages/<name>.py)."""
    for c in candidates:
        if window.page(c) is not None:
            return c
    for key, spec in getattr(window, "specs", {}).items():
        mod = getattr(spec.factory, "__module__", "") or ""
        if any(mod.endswith("." + c) for c in candidates):
            return key
    return None


class WhatsNewDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(_("What's new in 2.0"))
        self.resize(860, 620)
        lay = QVBoxLayout(self)
        lay.addWidget(heading(_("New in the {suite} {version}").format(suite=SUITE_NAME, version=__version__),
                              _("A lot is a lot, more is better: everything for the FM-1 now in one app. "
                                "A click on a card opens the area.")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setSpacing(12)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        self.cards: dict[str, QPushButton | None] = {}
        n = 0
        for title, text, ic, cands in NEWS:
            target = self._target(cands)
            card = QFrame()
            card.setObjectName("Tile")
            v = QVBoxLayout(card)
            h = QHBoxLayout()
            il = QLabel()
            il.setPixmap(icon(ic).pixmap(22, 22))
            h.addWidget(il)
            title = _(title)
            t = QLabel(title)
            t.setProperty("role", "cardTitle")
            h.addWidget(t, 1)
            v.addLayout(h)
            v.addWidget(_p(_(text), "muted"))
            if target:
                b = QPushButton(_("Open"))
                b.setProperty("variant", "flat")
                b.clicked.connect(lambda _c=False, tg=target: self._open(tg))
                v.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
                self.cards[title] = b
            else:
                v.addWidget(_p(_("not included in this edition"), "small"))
                self.cards[title] = None
            grid.addWidget(card, n // 3, n % 3)
            n += 1
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton(_("Let's go"))
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)

    def _target(self, cands):
        if cands and cands[0].startswith("@"):
            return cands[0]
        return find_page(self.window, cands)

    def _open(self, target: str):
        self.accept()
        w = self.window
        if target == "@palette":
            w.open_palette()
        elif target == "@syscheck":
            w.open_syscheck()
        elif target == "@settings":
            w._settings()
        else:
            w.go(target)


__all__ = ["FirstRunWizard", "WhatsNewDialog", "find_page", "Path"]
