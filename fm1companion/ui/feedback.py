# SPDX-License-Identifier: GPL-3.0-only
"""Klare Rückmeldung nach jeder Aktion: was passiert ist – und was NICHT passiert ist.

Baut auf ui/common.JobDialog auf. Kennt JobDialog schon `done_text=` (Erweiterung aus dem Rahmen), wird es
benutzt; sonst setzt dieser Helfer selbst die Überschrift („… – fertig: <Ergebnis>“), schreibt das Ergebnis
als letzte Protokollzeile und hängt optionale Folge-Knöpfe an (z. B. „Jetzt installieren …“, „Ordner öffnen“).
"""
from __future__ import annotations

import inspect
from typing import Callable

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialogButtonBox

from .common import JobDialog
from ..i18n import _


def _supports(name: str) -> bool:
    try:
        return name in inspect.signature(JobDialog.__init__).parameters
    except (TypeError, ValueError):
        return False


def mark_done(d: JobDialog, text: str, actions=()):
    """Ergebnis sichtbar machen (Überschrift, letzte Protokollzeile, Folge-Knöpfe)"""
    d.summary = text
    try:
        d.head.setText(_("{title} – done").format(title=d.windowTitle()))
        d.head.setToolTip(text)
        d.add("")
        d.add(_("RESULT: {text}").format(text=text))
        for label, fn in actions or ():
            b = d.buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            b.clicked.connect(lambda _c=False, f=fn, dd=d: (dd.accept(), f()))
    except RuntimeError:                       # Dialog schon weg
        pass


def run(parent, title: str, fn: Callable, *args, done: Callable[[object], str] | str = "",
        actions=(), on_done=None, dialog_cls=JobDialog, **kw) -> JobDialog:
    """JobDialog mit Ergebnis-Satz. done: Text oder fn(Ergebnis) -> Text; actions: [(Knopf, fn(Ergebnis))]."""
    if _supports("done_text"):                 # Rahmen-JobDialog mit Fertig/Fehlgeschlagen-Zustand
        d = dialog_cls(parent, title, fn, *args, on_done=on_done, done_text=done or None,
                       actions=list(actions or ()), **kw)
        d.exec()
        return d
    holder: dict = {}

    def finished(res):
        if on_done:
            on_done(res)
        text = done(res) if callable(done) else done
        acts = [(lab, (lambda f=f, r=res: f(r))) for lab, f in (actions or ())]
        holder["pending"] = (text, acts)
        d = holder.get("d")
        if d is not None and text:
            mark_done(d, *holder.pop("pending"))
    d = dialog_cls(parent, title, fn, *args, on_done=finished, **kw)
    holder["d"] = d
    if holder.get("pending") and holder["pending"][0]:      # schon fertig, bevor holder gesetzt war
        mark_done(d, *holder.pop("pending"))
    d.exec()
    return d


def open_folder(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def open_folder_action(path_of: Callable[[object], object], label: str | None = None):
    """Knopf „Ordner öffnen“; path_of(Ergebnis) -> Ordner"""
    return (label or _("Open folder"), lambda res: open_folder(path_of(res)))
