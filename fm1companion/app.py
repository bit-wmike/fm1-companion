# SPDX-License-Identifier: GPL-3.0-only
"""Startpunkt der Desktop-App."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from .i18n import _


def check() -> int:
    """Abhängigkeiten prüfen (für install.sh, Installer und Einrichtungs-Assistent). Liste: data/deps.json."""
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
    """apt-Befehl für alles Fehlende aus data/deps.json (einzige Paketliste – vorher stand hier eine eigene,
    unvollständige Liste ohne qtsvg, numpy und git)."""
    try:
        from . import diag
        return diag.apt_command(diag.check_deps(), include_optional=False) or "sudo apt install python3-pyqt6"
    except Exception:  # noqa: BLE001 – der Hinweis selbst darf nie scheitern
        return "sudo apt install python3-pyqt6 python3-pyqt6.qtsvg python3-pyqt6.qtwebengine python3-pyqt6.qtwebchannel"


def syscheck_cli() -> int:
    from . import config, syscheck
    found, meta = syscheck.run_all(config.load().root)
    print(syscheck.report(found, meta))
    return 1 if any(f.level == "error" for f in found) else 0


XDG_VARS = (("XDG_CONFIG_HOME", ".config", "config"), ("XDG_DATA_HOME", ".local/share", "data"),
            ("XDG_CACHE_HOME", ".cache", "cache"))


def isolate_demo(env=None, home=None) -> dict:
    """Demo-Modus: eigene Einstellungen, Daten und eigener Hauptordner – die echten bleiben unberührt.

    Muss VOR dem ersten Import von config laufen (config liest die XDG-Ordner beim Import).
    FM1C_DEMO_KEEP_XDG=1 (Tests, Sandkasten mit eigenen XDG-Ordnern) lässt die XDG-Ordner, wie sie sind;
    der Demo-Hauptordner kommt dann in deren Cache-Ordner. -> die gesetzten Werte"""
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
        print(_("Usage: fm1-companion [OPTION]\n\n"
                "  --demo       start with three simulated FM-1 (separate settings, your data stays untouched)\n"
                "  --check      check system packages and show the apt command for anything missing\n"
                "  --syscheck   check USB, power and audio (like Tools › System check)\n"
                "  --lang LANG  user interface language for this start: en, de (default: setting/system)\n"
                "  --no-splash  start without splash screen\n"
                "  --debug      verbose log (~/.local/state/fm1-companion/logs)\n"
                "  --version    show version\n"
                "  -h, --help   this help"))
        return 0
    if "--demo" in sys.argv:
        os.environ["FM1C_DEMO"] = "1"
        sys.argv.remove("--demo")
    if os.environ.get("FM1C_DEMO") == "1":
        isolate_demo()
    try:
        from PyQt6.QtCore import Qt, QCoreApplication
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print(_("PyQt6 is missing. Install it with:\n  {command}").format(command=missing_apt_hint()), file=sys.stderr)
        return 1
    try:                                       # muss vor der QApplication geladen werden
        import PyQt6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        pass
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    from . import APP_ID, APP_NAME, __version__, applog, config
    import logging
    applog.setup(logging.DEBUG if "--debug" in sys.argv else logging.INFO)
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
    if not pr.get("design_migrated"):           # 1.x-Standard "system" -> neues App-Design (automatisch hell/dunkel)
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
    auto_quit = os.environ.get("FM1C_AUTOQUIT_MS", "")
    if auto_quit.isdigit():                    # nur für Tests: die App nach n ms wie über „Beenden“ schließen
        def _auto_close():
            for d in app.topLevelWidgets():    # offene Dialoge (Assistent, Was ist neu) zuerst
                if d is not w and d.isVisible() and hasattr(d, "reject"):
                    d.reject()
            w.close()
        QTimer.singleShot(int(auto_quit), _auto_close)
    rc = app.exec()
    # geordnet abbauen: erst das Fenster (mit allen Seiten, Web-Views und Threads), dann die QApplication.
    # Überlässt man das dem Aufräumen beim Verlassen der Funktion, zerstört Python die QApplication
    # vor dem Fenster -> Absturz (Signal 11) beim Beenden, mit QtWebEngine zuverlässig.
    holder.clear()
    try:
        w.shutdown()
        w.deleteLater()
        del w
        app.processEvents()
        app.sendPostedEvents(None, 0)
        app.processEvents()
    except Exception:  # noqa: BLE001 – Beenden darf nie an einer Ausnahme scheitern
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
