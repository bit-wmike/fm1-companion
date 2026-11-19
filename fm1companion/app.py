# SPDX-License-Identifier: GPL-3.0-only
"""Entry point of the desktop app."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from .i18n import _


def check() -> int:
    """Check dependencies (for install.sh, the installer and the setup wizard). List: data/deps.json."""
    from . import __version__, diag
    print(f"FM-1 Companion {__version__}, Python {sys.version.split()[0]}")
    states = diag.check_deps()
    bad = 0
    for s in states:
        if s.ok:
            print(f"  ok         {s.package}")
        elif s.required:
            bad += 1
            print(_("  MISSING    {package}  ({why})").format(package=s.package, why=s.why))
        elif s.package in diag.UI_LANGUAGE_PACKAGES:
            # listed as required for the packages (so that switching the language works), not needed in English
            print(_("  (optional) {package}  (only needed when the user interface is not English: {why})").format(
                package=s.package, why=s.why))
        else:
            print(f"  (optional) {s.package}  ({s.why})")
    cmd = diag.apt_command(states)
    if cmd:
        print(_("Install with: {command}").format(command=cmd))
    ins, outs, err = diag.midi_ports()
    print(_("  MIDI outputs: {ports}").format(ports=", ".join(outs) or _("none")) if not err
          else _("  MIDI not usable: {error}").format(error=err))
    return 1 if bad else 0


def missing_apt_hint() -> str:
    """apt command for everything missing from data/deps.json (the only package list – previously there was a separate,
    incomplete list here without qtsvg, numpy and git)."""
    try:
        from . import diag
        return diag.apt_command(diag.check_deps(), include_optional=False) or "sudo apt install python3-pyqt6"
    except Exception:  # noqa: BLE001 – the hint itself must never fail
        return "sudo apt install python3-pyqt6 python3-pyqt6.qtsvg python3-pyqt6.qtwebengine python3-pyqt6.qtwebchannel"


def syscheck_cli() -> int:
    from . import config, syscheck
    found, meta = syscheck.run_all(config.load().root)
    print(syscheck.report(found, meta))
    return 1 if any(f.level == "error" for f in found) else 0


XDG_VARS = (("XDG_CONFIG_HOME", ".config", "config"), ("XDG_DATA_HOME", ".local/share", "data"),
            ("XDG_CACHE_HOME", ".cache", "cache"))


def isolate_demo(env=None, home=None) -> dict:
    """Demo mode: separate settings, data and main folder – the real ones stay untouched.

    Must run BEFORE the first import of config (config reads the XDG folders on import).
    FM1C_DEMO_KEEP_XDG=1 (tests, sandbox with its own XDG folders) leaves the XDG folders as they are;
    the demo main folder then goes into their cache folder. -> the values that were set"""
    env = os.environ if env is None else env
    home = Path(home) if home else Path.home()
    own = env.get("FM1C_DEMO_KEEP_XDG") == "1"
    cache_base = Path(env.get("XDG_CACHE_HOME") or home / ".cache") / "fm1-companion"
    out = {}
    if not own:
        demo_home = cache_base / "demo-home"
        for var, _d, sub in XDG_VARS:
            out[var] = str(demo_home / sub)
    if not env.get("FM1C_DEMO_ROOT"):
        out["FM1C_DEMO_ROOT"] = str(cache_base / "demo-FM-1")
    env.update(out)
    return out


def _creatable(folder: Path) -> bool:
    """Could ``folder`` be created and written to? Looks only – creates nothing."""
    p = folder
    while True:
        try:
            if p.exists():
                return p.is_dir() and os.access(p, os.W_OK | os.X_OK)
        except OSError:
            pass
        if p.parent == p:
            return False
        p = p.parent


def fallback_dirs(env=None, home=None) -> list[str]:
    """Data or cache folder out of reach (HOME read-only, ~/.cache is a file …): point them to a temporary
    folder for this run, so that the app starts instead of ending with a traceback at the first mkdir.

    Like isolate_demo() this must run BEFORE the first import of config. The settings folder is left alone:
    config.save() reports that case itself. -> the folders that were replaced (for the note in the log)"""
    from . import APP_ID
    env = os.environ if env is None else env
    home = Path(home) if home else Path.home()
    lost = [(var, Path(env.get(var) or home / default) / APP_ID) for var, default, _sub in XDG_VARS[1:]]
    lost = [(var, folder) for var, folder in lost if not _creatable(folder)]
    if lost:
        import atexit
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix=f"{APP_ID}-"))
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        for var, _folder in lost:
            env[var] = str(tmp / var.split("_")[1].lower())
    return [str(folder) for _var, folder in lost]


def take_lang_option(argv: list[str]) -> str | None:
    """remove ``--lang X`` / ``--lang=X`` from argv -> X (None if not given)"""
    for i, a in enumerate(list(argv)):
        if a == "--lang" and i + 1 < len(argv):
            val = argv[i + 1]
            del argv[i:i + 2]
            return val
        if a.startswith("--lang="):
            del argv[i]
            return a.split("=", 1)[1]
    return None


def main() -> int:
    lang = take_lang_option(sys.argv)
    if lang:                                   # before anything is translated (also --help)
        from . import i18n
        i18n.set_language(lang)
    if "--version" in sys.argv:
        from . import __version__
        print(__version__)
        return 0
    if "--check" in sys.argv:
        return check()
    if "--syscheck" in sys.argv:
        return syscheck_cli()
    if "--help" in sys.argv or "-h" in sys.argv:
        from .i18n import available_languages
        print(_("Usage: fm1-companion [OPTION]\n\n"
                "  --demo       start with three simulated FM-1 (separate settings, your data stays untouched)\n"
                "  --check      check system packages and show the apt command for anything missing\n"
                "  --syscheck   check USB, power and audio (like Tools › System check)\n"
                "  --lang LANG  user interface language for this start: {languages} (default: setting/system)\n"
                "  --no-splash  start without splash screen\n"
                "  --debug      verbose log (~/.local/state/fm1-companion/logs)\n"
                "  --version    show version\n"
                "  -h, --help   this help").format(languages=", ".join(available_languages())))
        return 0
    if "--demo" in sys.argv:
        os.environ["FM1C_DEMO"] = "1"
        sys.argv.remove("--demo")
    if os.environ.get("FM1C_DEMO") == "1":
        isolate_demo()
    lost_dirs = fallback_dirs()
    try:
        from PyQt6.QtCore import Qt, QCoreApplication
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print(_("PyQt6 is missing. Install it with:\n  {command}").format(command=missing_apt_hint()), file=sys.stderr)
        return 1
    try:                                       # must be loaded before the QApplication
        import PyQt6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        pass
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    from . import APP_ID, APP_NAME, __version__, applog, config
    import logging
    applog.setup(logging.DEBUG if "--debug" in sys.argv else logging.INFO)
    note = ""
    if lost_dirs:
        note = _("The app cannot write to {folders}. It uses a temporary folder for this run: what it stores "
                 "there (your device profiles, downloaded editors and tools, the sample index, the record of "
                 "firmware installations) is gone when you quit. Better not install firmware like this.").format(
                     folders=", ".join(lost_dirs))
        logging.getLogger("fm1companion").warning(note)
        print(note, file=sys.stderr)
    existed = config.CONFIG_FILE.exists()
    app = QApplication(sys.argv)
    from PyQt6.QtCore import QLocale
    from .ui.common import qt_locale
    QLocale.setDefault(qt_locale())            # number format (decimal comma in spin boxes) of the UI language
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_ID)
    app.setDesktopFileName(APP_ID)
    config.ensure_dirs()
    from .ui import theme
    settings = config.load()
    pr = theme.prefs()
    if not pr.get("design_migrated"):           # 1.x default "system" -> new app theme (automatic light/dark)
        pr["design_migrated"] = True
        if settings.theme == "system":
            settings.theme = "auto"
        config.save(settings)
    theme.apply(app, settings.theme)
    try:                                       # Qt's own texts (file dialogs, standard buttons) in the UI language
        from .ui.common import install_translations
        install_translations(app)
    except Exception:  # noqa: BLE001
        pass
    from .ui.crash import CrashHandler
    holder: dict = {}
    crash = CrashHandler(lambda: holder.get("w")).install()
    splash = None
    if pr.get("splash", True) and "--no-splash" not in sys.argv:
        from .ui.splash import Splash
        splash = Splash()
        splash.show()
        splash.step(_("Starting …"))
    from .ui.main_window import MainWindow
    w = MainWindow(progress=splash.step if splash else None)
    holder["w"] = w
    w._was_installed_before = existed
    w.crash_handler = crash
    w.show()
    if splash:
        splash.finish(w)
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(400, w.maybe_first_run)
    if note:
        QTimer.singleShot(1600, lambda: w.notify(note, "warning"))
    auto_quit = os.environ.get("FM1C_AUTOQUIT_MS", "")
    if auto_quit.isdigit():                    # tests only: close the app after n ms as if via "Quit"
        def _auto_close():
            for d in app.topLevelWidgets():    # open dialogs (wizard, What's new) first
                if d is not w and d.isVisible() and hasattr(d, "reject"):
                    d.reject()
            w.close()
        QTimer.singleShot(int(auto_quit), _auto_close)
    rc = app.exec()
    # tear down in order: first the window (with all pages, web views and threads), then the QApplication.
    # If this is left to the cleanup when leaving the function, Python destroys the QApplication
    # before the window -> crash (signal 11) on exit, reliably with QtWebEngine.
    holder.clear()
    teardown(app, w)
    del w
    return rc


def teardown(app, w) -> None:
    """After app.exec(): delete the window for real, then the QApplication – before main() returns.

    deleteLater() alone is not enough here: outside the event loop Qt dispatches DeferredDelete only when it is
    asked for by type (sendPostedEvents(None, 0) skips it), so the window with its ~1900 widgets used to survive
    until Python's exit. PyQt's own atexit handler then walks every wrapper while the QApplication still lives
    (a closure – here the FM1C_AUTOQUIT_MS timer – is enough to keep it alive) and deletes the Python-owned
    objects one by one; their destructors change the wrapper map it is walking -> sip_api_get_address() on a
    stale wrapper, signal 11 in about 1 of 5 quits (test_appstart). Deleting both here leaves that handler
    nothing to do."""
    from PyQt6 import sip
    from PyQt6.QtCore import QEvent
    try:
        w.shutdown()
        w.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        if not sip.isdeleted(w):
            sip.delete(w)
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    except Exception:  # noqa: BLE001 – quitting must never fail on an exception
        pass
    try:                                       # (the web profile is a child of the app: its pages are gone by now)
        if not sip.isdeleted(app):
            sip.delete(app)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    sys.exit(main())
