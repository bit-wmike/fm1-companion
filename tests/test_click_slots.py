# SPDX-License-Identifier: GPL-3.0-only
"""Qt's clicked/triggered signals pass ``checked`` (False) to a slot that can take an argument. A slot whose first
parameter is optional and means something else (``path=None``, ``params=None`` …) then gets False as real input –
that crashed the Studio's export, open-backup and write-back buttons on every click, while all tests called the
methods directly. This scan finds every such connection; a slot must either take no argument, take a bool, or be
connected through a lambda. Reviewed exceptions (the slot treats a falsy value like "nothing given") are listed."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNALS = {"clicked", "triggered"}
HELPERS = {"button", "_btn", "tool_button", "_tool", "_action", "action", "addAction"}

# file:method – read one by one: each uses ``if not x`` / ``x or default`` or really takes the bool
REVIEWED: set[str] = {
    "fm1companion/ui/firmware_page.py:_install_local",        # if not path: file dialog
    "fm1companion/ui/pages/compose.py:learn_markov",          # if not path
    "fm1companion/ui/pages/compose.py:save_midi",             # if not path
    "fm1companion/ui/pages/presets.py:import_files",          # if not files
    "fm1companion/ui/pages/project_view.py:export_csv",       # path or self._target(...)
    "fm1companion/ui/pages/project_view.py:export_json",
    "fm1companion/ui/pages/project_view.py:export_midi",
    "fm1companion/ui/pages/project_view.py:export_png",
    "fm1companion/ui/pages/project_view.py:open_backup",      # if not path
    "fm1companion/ui/pages/project_view.py:open_project",     # if not path
    "fm1companion/ui/pages/simulator.py:start",               # s or self.selected()
    "fm1companion/ui/pages/voices.py:import_files",           # if not files
    "fm1companion/ui/playback_bar.py:stop",                   # reason or default text
    "fm1companion/ui/statusbar.py:save",                      # if not path
}


def _optional_first(fn: ast.FunctionDef) -> bool:
    """first parameter after self has a default and is not annotated/used as bool"""
    args = fn.args.args[1:] if fn.args.args and fn.args.args[0].arg in ("self", "cls") else fn.args.args
    if not args:
        return False
    n_def = len(fn.args.defaults)
    if n_def < len(args):            # first parameter is required: Qt passes checked, a plain TypeError shows at once
        return False
    a, d = args[0], fn.args.defaults[0]
    if isinstance(a.annotation, ast.Name) and a.annotation.id == "bool":
        return False
    if isinstance(d, ast.Constant) and isinstance(d.value, bool):
        return False
    return True


def scan() -> list[str]:
    out = []
    for f in sorted((ROOT / "fm1companion" / "ui").rglob("*.py")):
        tree = ast.parse(f.read_text("utf-8"))
        risky = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and _optional_first(n)}

        def is_risky(node) -> str:
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" \
                    and node.attr in risky:
                return node.attr
            return ""
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func
            cands = []
            if isinstance(fn, ast.Attribute) and fn.attr == "connect" and isinstance(fn.value, ast.Attribute) \
                    and fn.value.attr in SIGNALS:
                cands = n.args
            elif (isinstance(fn, ast.Name) and fn.id in HELPERS) or \
                    (isinstance(fn, ast.Attribute) and fn.attr in HELPERS):
                cands = list(n.args) + [k.value for k in n.keywords]
            for c in cands:
                name = is_risky(c)
                if name:
                    out.append(f"{f.relative_to(ROOT)}:{name}")
    return sorted(set(out))


def test_no_slot_takes_checked_as_data():
    found = [x for x in scan() if x not in REVIEWED]
    assert not found, ("clicked/triggered connected to a slot whose optional first parameter would receive "
                       "checked=False – connect through a lambda or add it to REVIEWED after reading it:\n  "
                       + "\n  ".join(found))


def test_reviewed_list_is_current():
    stale = sorted(REVIEWED - set(scan()))
    assert not stale, f"no longer needed in REVIEWED: {stale}"
