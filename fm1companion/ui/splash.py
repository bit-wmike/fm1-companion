# SPDX-License-Identifier: GPL-3.0-only
"""Splash screen: logo, name, version and what is currently loading."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen

from .. import SUITE_NAME, __version__
from ..i18n import _

W, H = 520, 300


def _pixmap(dpr: float) -> QPixmap:
    pm = QPixmap(int(W * dpr), int(H * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, 0, H)
    g.setColorAt(0, QColor("#2a2c31"))
    g.setColorAt(1, QColor("#131417"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(0, 0, W, H), 18, 18)
    a = QLinearGradient(0, 0, W, 0)
    a.setColorAt(0, QColor("#ffb347"))
    a.setColorAt(0.6, QColor("#ff7a1a"))
    a.setColorAt(1, QColor("#e8452c"))
    p.setBrush(a)
    p.drawRoundedRect(QRectF(0, H - 6, W, 6), 3, 3)
    from .main_window import app_icon
    icon_pm = app_icon().pixmap(96, 96)
    p.drawPixmap(36, 52, 96, 96, icon_pm)
    f = QFont(QApplication.font())
    f.setPointSizeF(f.pointSizeF() * 2.1 if f.pointSizeF() > 0 else 20)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#f2f3f5"))
    name, _sep, suite = SUITE_NAME.rpartition(" ")
    p.drawText(QRectF(150, 58, W - 170, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
    f2 = QFont(f)
    f2.setBold(False)
    f2.setPointSizeF(f.pointSizeF() * 0.62)
    p.setFont(f2)
    p.setPen(QColor("#ff9a4a"))
    p.drawText(QRectF(150, 98, W - 170, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               f"{suite} {__version__}")
    f3 = QFont(QApplication.font())
    p.setFont(f3)
    p.setPen(QColor("#9aa0a8"))
    p.drawText(QRectF(150, 128, W - 170, 24), Qt.AlignmentFlag.AlignLeft, _("Everything for the M-VAVE FM-1 in one app"))
    p.end()
    return pm


class Splash(QSplashScreen):
    def __init__(self):
        app = QApplication.instance()
        super().__init__(_pixmap(app.devicePixelRatio() if app else 1.0))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.steps = []

    def step(self, text: str):
        self.steps.append(text)
        self.showMessage(text, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, QColor("#c9cdd3"))
        QApplication.processEvents()

    def drawContents(self, painter):
        painter.setPen(QColor("#c9cdd3"))
        painter.drawText(QRectF(36, H - 46, W - 72, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.message())
