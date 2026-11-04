#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Check the translations and measure how much of the code still has German UI text.

  python3 tools/i18n_check.py                 # all checks + short summary (exit 1 on errors)
  python3 tools/i18n_check.py --lang de       # only one language
  python3 tools/i18n_check.py --german-left   # only the "German left in source" scan, counts per file
  python3 tools/i18n_check.py --list          # also list untranslated / missing / obsolete msgids

Errors (exit code 1):
  * a .po file cannot be parsed
  * placeholders differ: every ``{name}`` / ``%s`` / ``%(name)s`` of the msgid must appear in the msgstr,
    and the msgstr must not use a placeholder the msgid does not have (that would crash ``.format()``)
  * the same msgid (and msgctxt) appears in two .po files of one language with different msgstr
A line with the comment ``# i18n: ignore`` is left out of the German-left scan (for German text that must
stay as it is, e.g. names of files on the device). Reported, not errors: untranslated (empty or fuzzy) entries, msgids missing from a language,
obsolete entries (no longer in the code), and the German-left heuristic.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fm1companion import i18n  # noqa: E402
import i18n_extract  # noqa: E402

PKG = ROOT / "fm1companion"
LOCALE = PKG / "locale"

_BRACE = re.compile(r"(?<!\{)\{([^{}]*)\}(?!\})")
_PERCENT = re.compile(r"%(?:\([^)]+\))?[-#0 +]*\d*(?:\.\d+)?[sdifrxXeEgGc%]")


def placeholders(s: str) -> Counter:
    out = Counter()
    for m in _BRACE.finditer(s.replace("{{", "").replace("}}", "")):
        out["{" + m.group(1).split("!")[0].split(":")[0] + "}"] += 1
    for m in _PERCENT.finditer(s):
        if m.group(0) != "%%":
            out[m.group(0)] += 1
    return out


def check_placeholders(e: i18n.POEntry) -> list[str]:
    """problems of one entry (empty list = fine)"""
    probs = []
    src = set(placeholders(e.msgid))
    if e.msgid_plural is not None:
        src |= set(placeholders(e.msgid_plural))
    for i, s in enumerate(e.msgstr):
        if not s:
            continue
        have = set(placeholders(s))
        extra = have - src
        if extra:
            probs.append(f"msgstr{f'[{i}]' if e.msgid_plural is not None else ''} has unknown placeholder(s) "
                         f"{', '.join(sorted(extra))}")
        if e.msgid_plural is None:
            missing = src - have
            if missing:
                probs.append(f"msgstr lacks placeholder(s) {', '.join(sorted(missing))}")
        else:                                  # plural forms may drop {n} ("one file"), but not the others
            missing = {p for p in src - have if p not in ("{n}", "%d", "{count}")}
            if missing:
                probs.append(f"msgstr[{i}] lacks placeholder(s) {', '.join(sorted(missing))}")
    return probs


def languages() -> list[str]:
    return sorted(d.name for d in LOCALE.iterdir() if d.is_dir() and any(d.glob("*.po"))) if LOCALE.is_dir() else []


def check_language(lang: str, known: set, verbose: bool = False) -> tuple[list[str], dict]:
    errors: list[str] = []
    seen: dict[tuple, tuple[str, list[str]]] = {}
    untranslated, obsolete = [], []
    for f in sorted((LOCALE / lang).glob("*.po")):
        rel = f.relative_to(ROOT).as_posix()
        try:
            entries = i18n.parse_po(f.read_text("utf-8"), rel)
        except (i18n.POError, UnicodeDecodeError) as e:
            errors.append(f"{rel}: {e}")
            continue
        for e in entries:
            if e.msgid == "":
                continue
            for p in check_placeholders(e):
                errors.append(f"{rel}:{e.line}: {p}: {e.msgid[:60]!r}")
            if e.key in seen and seen[e.key][1] != e.msgstr:
                errors.append(f"{rel}:{e.line}: {e.msgid[:60]!r} also in {seen[e.key][0]} with a different msgstr")
            seen.setdefault(e.key, (rel, e.msgstr))
            if e.fuzzy or not e.translated:
                untranslated.append(e.msgid)
            if e.key not in known:
                obsolete.append(e.msgid)
    missing = [k[1] for k in known if k not in seen]
    return errors, {"entries": len(seen), "untranslated": untranslated, "obsolete": obsolete, "missing": missing}


# ====================================================================== German left in the source
GERMAN_WORDS = re.compile(
    r"\b(und|oder|nicht|kein|keine|keinen|ist|sind|wird|werden|wurde|mit|für|auf|beim|zum|zur|vom|eine|einen|"
    r"einem|der|die|das|den|dem|des|bitte|jetzt|noch|schon|neu|alle|alles|nur|auch|wenn|dann|hier|dein|deine|"
    r"deinen|ihr|Gerät|Datei|Dateien|Ordner|Fehler|Seite|Einstellungen|Speichern|Laden|Abbrechen|Schließen|"
    r"Öffnen|Löschen|Zurück|Weiter|fertig|läuft|gespeichert|geladen|verbunden|getrennt|gefunden|fehlt)\b")
UMLAUT = re.compile(r"[äöüÄÖÜß]")
TRANSLATE_FUNCS = set(i18n_extract.KEYWORDS)
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
LOG_OBJECTS = {"log", "logger", "logging", "_log", "LOG"}


def looks_german(s: str) -> bool:
    if not s.strip() or not re.search(r"[A-Za-zÄÖÜäöü]{2}", s):
        return False
    return bool(UMLAUT.search(s) or GERMAN_WORDS.search(s))


def _docstring_nodes(tree) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _skip_calls(tree) -> set[int]:
    """ids of string nodes that are fine as they are: inside _()/N_()… or log calls"""
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = i18n_extract._name(f)
        is_log = isinstance(f, ast.Attribute) and f.attr in LOG_METHODS and (
            (isinstance(f.value, ast.Name) and f.value.id in LOG_OBJECTS)
            or (isinstance(f.value, ast.Call) and isinstance(f.value.func, ast.Attribute)
                and f.value.func.attr == "getLogger"))
        if name in TRANSLATE_FUNCS or is_log:
            for a in list(node.args) + [k.value for k in node.keywords]:
                for sub in ast.walk(a):
                    ids.add(id(sub))
        for k in node.keywords:                # search keywords stay in both languages (PageSpec(keywords=…))
            if k.arg == "keywords":
                for sub in ast.walk(k.value):
                    ids.add(id(sub))
    return ids


def german_left_in_source(text: str, rel: str = "") -> list[tuple[int, str]]:
    """string literals that look German and are not wrapped in _() (docstrings and log calls excluded)"""
    tree = ast.parse(text, rel)
    skip = _docstring_nodes(tree) | _skip_calls(tree)
    lines = text.splitlines()
    ignored = {i for i, ln in enumerate(lines, 1) if "i18n: ignore" in ln}
    inner: set[int] = set()
    hits = []
    for node in ast.walk(tree):
        if id(node) in skip or id(node) in inner:
            continue
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                inner.add(id(v))
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                elif isinstance(v, ast.FormattedValue):
                    for sub in ast.walk(v):
                        inner.add(id(sub))
                    parts.append("{}")
            s = "".join(parts)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
        else:
            continue
        if looks_german(s) and node.lineno not in ignored:
            hits.append((node.lineno, s))
    return sorted(hits)


def german_left(pkg: Path = PKG) -> dict[str, list[tuple[int, str]]]:
    out = {}
    for f in i18n_extract.source_files(pkg):
        if f.name == "i18n.py":
            continue
        rel = f.relative_to(pkg.parent).as_posix()
        try:
            hits = german_left_in_source(f.read_text("utf-8"), rel)
        except SyntaxError:
            continue
        if hits:
            out[rel] = hits
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="check translations; measure German text left in the code")
    ap.add_argument("--lang", action="append", help="language(s) to check (default: all)")
    ap.add_argument("--german-left", action="store_true", help="only the German-left scan")
    ap.add_argument("--show", action="store_true", help="with --german-left: print each string")
    ap.add_argument("--list", action="store_true", help="list untranslated, missing and obsolete msgids")
    a = ap.parse_args(argv)
    if not a.german_left:
        warnings: list[str] = []
        msgs = i18n_extract.extract(i18n_extract.PKG, warnings)
        known = {(m.msgctxt, m.msgid) for m in msgs}
        for w in warnings:
            print("warning:", w)
        bad = 0
        for lang in a.lang or languages():
            errors, st = check_language(lang, known)
            bad += len(errors)
            for e in errors:
                print("ERROR", e)
            print(f"[{lang}] {st['entries']} entries, {len(known) - len(st['missing'])}/{len(known)} source "
                  f"messages translated, {len(st['untranslated'])} untranslated/fuzzy, {len(st['missing'])} missing, "
                  f"{len(st['obsolete'])} obsolete")
            if a.list:
                for k in ("untranslated", "missing", "obsolete"):
                    for m in st[k]:
                        print(f"  {k}: {m[:100]!r}")
    left = german_left()
    total = sum(len(v) for v in left.values())
    print(f"German left in source: {total} strings in {len(left)} files")
    if a.german_left:
        width = max((len(k) for k in left), default=10)
        for rel, hits in sorted(left.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            print(f"  {len(hits):5d}  {rel:<{width}}")
            if a.show:
                for line, s in hits:
                    print(f"           {line}: {s[:90]!r}")
    if a.german_left:
        return 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
