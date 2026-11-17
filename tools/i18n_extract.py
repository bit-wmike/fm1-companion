#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Extract translatable strings into fm1companion/locale/messages.pot.

Finds calls of ``_()``, ``N_()``, ``NP_()``, ``gettext()``, ``ngettext()``, ``pgettext()`` and ``npgettext()`` (also as
``i18n._(...)``) in fm1companion/**/*.py (without vendor/ and sims/) by reading the syntax tree – the code
is never imported or run. Only plain string literals are extracted; an f-string or a variable inside
``_()`` is reported as a warning (use ``_("… {name} …").format(name=…)`` instead).

  python3 tools/i18n_extract.py            # write fm1companion/locale/messages.pot
  python3 tools/i18n_extract.py --check    # exit 1 if messages.pot is out of date (CI)
  python3 tools/i18n_extract.py --stdout   # print instead of writing

A comment ``# Translators: …`` on the line(s) directly above the call is copied into the .pot.
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "fm1companion"
POT = PKG / "locale" / "messages.pot"
EXCLUDE = ("vendor", "sims", "__pycache__")

# function name -> (index of msgctxt or None, index of msgid, index of msgid_plural or None)
KEYWORDS = {"_": (None, 0, None), "N_": (None, 0, None), "NP_": (None, 0, 1), "gettext": (None, 0, None),
            "ngettext": (None, 0, 1), "pgettext": (0, 1, None), "npgettext": (0, 1, 2)}


@dataclass
class Message:
    msgid: str
    msgctxt: str | None = None
    msgid_plural: str | None = None
    refs: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)


def source_files(pkg: Path = PKG) -> list[Path]:
    return sorted(p for p in pkg.rglob("*.py")
                  if not any(part in EXCLUDE for part in p.relative_to(pkg).parts))


def _name(func) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "i18n":
        return func.attr
    return ""


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def extract_source(text: str, rel: str, warnings: list[str] | None = None) -> list[tuple]:
    """-> [(msgctxt, msgid, msgid_plural, line, comments)] for one source file"""
    warnings = [] if warnings is None else warnings
    tree = ast.parse(text, rel)
    lines = text.splitlines()
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kw = KEYWORDS.get(_name(node.func))
        if kw is None:
            continue
        ci, mi, pi = kw
        need = max(i for i in kw if i is not None) + 1
        if len(node.args) < need:
            continue
        vals = [(_const(node.args[i]) if i is not None else None) for i in kw]
        if any(v is None for v, i in zip(vals, kw) if i is not None):
            bad = node.args[mi]
            what = "f-string" if isinstance(bad, ast.JoinedStr) else "non-literal argument"
            warnings.append(f"{rel}:{node.lineno}: {what} in {_name(node.func)}() – not extracted")
            continue
        comments = []
        ln = node.lineno - 2
        while ln >= 0 and lines[ln].strip().startswith("#"):
            c = lines[ln].strip().lstrip("#").strip()
            if c.lower().startswith("translators:"):
                comments.insert(0, c)
            ln -= 1
        out.append((vals[0], vals[1], vals[2], node.lineno, comments))
    out.sort(key=lambda t: t[3])
    return out


def extract(pkg: Path = PKG, warnings: list[str] | None = None) -> list[Message]:
    msgs: dict[tuple, Message] = {}
    for f in source_files(pkg):
        rel = f.relative_to(pkg.parent).as_posix()
        try:
            found = extract_source(f.read_text("utf-8"), rel, warnings)
        except SyntaxError as e:
            if warnings is not None:
                warnings.append(f"{rel}: syntax error {e}")
            continue
        for ctx, mid, plural, line, comments in found:
            if mid == "":
                continue
            m = msgs.setdefault((ctx, mid), Message(mid, ctx, plural))
            if plural and not m.msgid_plural:
                m.msgid_plural = plural
            m.refs.append(f"{rel}:{line}")
            m.comments += [c for c in comments if c not in m.comments]
    return list(msgs.values())


def po_string(s: str, prefix: str) -> str:
    """msgid "…" in .po notation, multi-line strings split after each \\n"""
    from fm1companion.i18n import escape
    if "\n" not in s or s.endswith("\n") and s.count("\n") == 1:
        return f'{prefix} "{escape(s)}"\n'
    parts = s.split("\n")
    chunks = [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    return f'{prefix} ""\n' + "".join(f'"{escape(c)}"\n' for c in chunks)


def format_entry(m: Message, msgstr: list[str] | None = None, refs: bool = True) -> str:
    out = ""
    for c in m.comments:
        out += f"#. {c}\n"
    if refs and m.refs:
        line = "#:"
        for r in m.refs:
            if len(line) + len(r) + 1 > 78 and line != "#:":
                out += line + "\n"
                line = "#:"
            line += " " + r
        out += line + "\n"
    flags = []
    if "{" in m.msgid and "}" in m.msgid:
        flags.append("python-brace-format")
    elif "%" in m.msgid and any(x in m.msgid for x in ("%s", "%d", "%(")):
        flags.append("python-format")
    if flags:
        out += "#, " + ", ".join(flags) + "\n"
    if m.msgctxt is not None:
        out += po_string(m.msgctxt, "msgctxt")
    out += po_string(m.msgid, "msgid")
    if m.msgid_plural is not None:
        out += po_string(m.msgid_plural, "msgid_plural")
        forms = msgstr or ["", ""]
        for i, s in enumerate(forms):
            out += po_string(s, f"msgstr[{i}]")
    else:
        out += po_string((msgstr or [""])[0], "msgstr")
    return out


POT_HEADER = '''# Translatable strings of FM-1 Companion (template – generated by tools/i18n_extract.py, do not edit).
# SPDX-License-Identifier: GPL-3.0-only
msgid ""
msgstr ""
"Project-Id-Version: FM-1 Companion\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: nplurals=INTEGER; plural=EXPRESSION;\\n"
'''


def render_pot(msgs: list[Message]) -> str:
    return POT_HEADER + "".join("\n" + format_entry(m) for m in msgs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if messages.pot is not up to date")
    ap.add_argument("--stdout", action="store_true", help="print the template instead of writing it")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    warnings: list[str] = []
    msgs = extract(PKG, warnings)
    for w in warnings:
        print("warning:", w, file=sys.stderr)
    text = render_pot(msgs)
    if a.stdout:
        sys.stdout.write(text)
        return 0
    if a.check:
        cur = POT.read_text("utf-8") if POT.exists() else ""
        if cur != text:
            print(f"{POT.relative_to(ROOT)} is out of date – run tools/i18n_extract.py", file=sys.stderr)
            return 1
        print(f"{POT.relative_to(ROOT)} is up to date ({len(msgs)} messages)")
        return 0
    POT.parent.mkdir(parents=True, exist_ok=True)
    POT.write_text(text, "utf-8")
    print(f"{POT.relative_to(ROOT)}: {len(msgs)} messages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
