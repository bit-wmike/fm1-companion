# SPDX-License-Identifier: GPL-3.0-only
"""One-time “I understand” dialog before the first install/rescue action (see fm1companion.risk)."""
from __future__ import annotations

import html

from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from .. import risk
from ..i18n import _


class RiskDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(risk.TITLE)
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        head = QLabel(risk.TITLE)
        head.setProperty("role", "pageTitle")
        lay.addWidget(head)
        body = QLabel("".join(f"<p>{html.escape(t)}</p>" for t in risk.TEXT_LINES))
        body.setWordWrap(True)
        lay.addWidget(body)
        self.check = QCheckBox(risk.CHECK_TEXT)
        lay.addWidget(self.check)
        self.buttons = QDialogButtonBox()
        self.ok = self.buttons.addButton(_("Continue"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = self.buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.ok.setEnabled(False)
        cancel.setDefault(True)
        self.check.toggled.connect(self.ok.setEnabled)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def _accept(self):
        if self.check.isChecked():
            self.accept()


def ensure_risk_ack(parent=None) -> bool:
    """True if the notice has already been acknowledged or is acknowledged now (then saved)."""
    if risk.acknowledged():
        return True
    dlg = RiskDialog(parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    risk.acknowledge()
    return True


__all__ = ["RiskDialog", "ensure_risk_ack"]
