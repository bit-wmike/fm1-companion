# SPDX-License-Identifier: GPL-3.0-only
"""Protokolldatei: ~/.local/state/fm1-companion/logs/fm1-companion.log (rotierend, 5 × 1 MB)."""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from . import APP_ID, __version__

_configured = False


def log_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / APP_ID / "logs"


def log_file() -> Path:
    return log_dir() / f"{APP_ID}.log"


def setup(level: int = logging.INFO) -> Path | None:
    """Python-Logging und Qt-Meldungen in die Datei leiten (einmal beim Start)."""
    global _configured
    if _configured:
        return log_file()
    root = logging.getLogger()
    root.setLevel(level)
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=4, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(h)
    except OSError as e:
        print(f"Protokoll nicht beschreibbar: {e}", file=sys.stderr)
        return None
    _configured = True
    logging.getLogger("fm1companion").info("Start %s %s, Python %s", APP_ID, __version__, sys.version.split()[0])
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
        qlog = logging.getLogger("qt")
        lv = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}

        def handler(mode, ctx, msg):
            qlog.log(lv.get(mode, logging.INFO), msg)
            if mode != QtMsgType.QtDebugMsg:
                print(msg, file=sys.stderr)
        qInstallMessageHandler(handler)
    except ImportError:
        pass
    return log_file()


def tail(lines: int = 400) -> str:
    try:
        data = log_file().read_text("utf-8", errors="replace").splitlines()
        return "\n".join(data[-lines:])
    except OSError:
        return ""
