# SPDX-License-Identifier: GPL-3.0-only
"""Translations for the user interface (no Qt, no build step).

English is the source language: every user-visible string in the code is English and wrapped in ``_()``.
Translations live in ``fm1companion/locale/<lang>/*.po`` (several files per language, one per area of the
app; they are merged). The .po files are read directly by the small parser below, so no .mo files and no
compile step are needed.

Language order: ``set_language()`` (explicit) > environment ``FM1C_LANG`` > setting ``language`` in the
config > system locale. ``"system"`` means: the system language if a translation exists, else English.

The language is fixed for the whole process (changing it in the settings takes effect after a restart),
so module-level ``_()`` calls are fine once the language is known. See docs/TRANSLATING.md.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

LOCALE_DIR = Path(__file__).resolve().parent / "locale"
SOURCE_LANGUAGE = "en"
SYSTEM = "system"
# names shown in the language selection (each in its own language)
LANGUAGE_NAMES = {"en": "English", "de": "Deutsch"}

_lock = threading.RLock()
_lang: str | None = None                      # resolved language code
_catalogs: dict[str, "Catalog"] = {}          # cache: language -> catalog


# ====================================================================== .po parser
@dataclass
class POEntry:
    msgid: str
    msgstr: list[str] = field(default_factory=list)   # [singular] or [form0, form1, …] for plurals
    msgctxt: str | None = None
    msgid_plural: str | None = None
    flags: set[str] = field(default_factory=set)
    references: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    line: int = 0
    file: str = ""

    @property
    def fuzzy(self) -> bool:
        return "fuzzy" in self.flags

    @property
    def translated(self) -> bool:
        return bool(self.msgstr) and all(s != "" for s in self.msgstr)

    @property
    def key(self) -> tuple[str | None, str]:
        return (self.msgctxt, self.msgid)


class POError(ValueError):
    pass


_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def unescape(s: str) -> str:
    """content of a C-style quoted .po string (without the quotes) -> text"""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(s):
            raise POError("backslash at end of string")
        c = s[i]
        if c in _ESC:
            out.append(_ESC[c])
            i += 1
        elif c in "01234567":
            j = i
            while j < len(s) and j < i + 3 and s[j] in "01234567":
                j += 1
            out.append(chr(int(s[i:j], 8)))
            i = j
        elif c == "x":
            j = i + 1
            while j < len(s) and s[j] in "0123456789abcdefABCDEF":
                j += 1
            if j == i + 1:
                raise POError("invalid \\x escape")
            out.append(chr(int(s[i + 1:j], 16)))
            i = j
        else:
            raise POError(f"unknown escape \\{c}")
    return "".join(out)


def escape(s: str) -> str:
    return (s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
            .replace("\r", "\\r"))


_KW = re.compile(r'^(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+(".*")\s*$')
_STR = re.compile(r'^(".*")\s*$')


def _quoted(tok: str, lineno: int) -> str:
    if len(tok) < 2 or not (tok.startswith('"') and tok.endswith('"')):
        raise POError(f"line {lineno}: expected a quoted string")
    body = tok[1:-1]
    # an unescaped quote inside the body is an error
    if re.search(r'(?<!\\)(?:\\\\)*"', body):
        raise POError(f"line {lineno}: stray quote")
    try:
        return unescape(body)
    except POError as e:
        raise POError(f"line {lineno}: {e}") from None


def parse_po(text: str, filename: str = "") -> list[POEntry]:
    """parse .po text into entries (the header entry msgid "" included; obsolete #~ entries skipped).
    Raises POError with the line number on syntax errors."""
    entries: list[POEntry] = []
    cur: POEntry | None = None
    pending_flags: set[str] = set()
    pending_refs: list[str] = []
    pending_comments: list[str] = []
    field_name: str | None = None
    field_idx = 0
    seen: set[str] = set()

    def finish():
        nonlocal cur, field_name
        if cur is not None:
            if "msgid" not in seen:
                raise POError(f"{filename}:{cur.line}: entry without msgid")
            if not cur.msgstr:
                raise POError(f"{filename}:{cur.line}: entry without msgstr")
            entries.append(cur)
        cur, field_name = None, None
        seen.clear()

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#~"):
                continue                               # obsolete entry
            if cur is not None and field_name is not None and "msgstr" in seen:
                finish()
            if line.startswith("#,"):
                pending_flags |= {f.strip() for f in line[2:].split(",") if f.strip()}
            elif line.startswith("#:"):
                pending_refs += line[2:].split()
            elif line.startswith("#|"):
                pass                                   # previous msgid
            else:
                pending_comments.append(line[1:].strip())
            continue
        m = _KW.match(line)
        if m:
            kw, idx, tok = m.group(1), m.group(2), m.group(3)
            base = "msgstr" if kw.startswith("msgstr") else kw
            if base in ("msgctxt", "msgid") and cur is not None and ("msgstr" in seen or base in seen
                                                                     or (base == "msgctxt" and "msgid" in seen)):
                finish()
            if cur is None:
                if base not in ("msgctxt", "msgid"):
                    raise POError(f"{filename}:{lineno}: {kw} without msgid")
                cur = POEntry(msgid="", line=lineno, file=filename, flags=pending_flags,
                              references=pending_refs, comments=pending_comments)
                pending_flags, pending_refs, pending_comments = set(), [], []
            val = _quoted(tok, lineno)
            if base == "msgctxt":
                cur.msgctxt = val
            elif base == "msgid":
                cur.msgid = val
            elif base == "msgid_plural":
                if "msgid" not in seen or "msgstr" in seen:
                    raise POError(f"{filename}:{lineno}: msgid_plural must follow msgid")
                cur.msgid_plural = val
            else:
                if "msgid" not in seen:
                    raise POError(f"{filename}:{lineno}: msgstr before msgid")
                field_idx = int(idx) if idx is not None else 0
                if idx is None and cur.msgid_plural is not None:
                    raise POError(f"{filename}:{lineno}: plural entry needs msgstr[n]")
                while len(cur.msgstr) <= field_idx:
                    cur.msgstr.append("")
                cur.msgstr[field_idx] = val
            seen.add(base)
            field_name = base
            continue
        m = _STR.match(line)
        if m and cur is not None and field_name is not None:
            val = _quoted(m.group(1), lineno)
            if field_name == "msgctxt":
                cur.msgctxt = (cur.msgctxt or "") + val
            elif field_name == "msgid":
                cur.msgid += val
            elif field_name == "msgid_plural":
                cur.msgid_plural = (cur.msgid_plural or "") + val
            else:
                cur.msgstr[field_idx] += val
            continue
        raise POError(f"{filename}:{lineno}: cannot parse: {raw[:60]!r}")
    finish()
    return entries


def parse_header(entries: list[POEntry]) -> dict[str, str]:
    for e in entries:
        if e.msgid == "" and e.msgctxt is None and e.msgstr:
            out = {}
            for line in e.msgstr[0].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    out[k.strip()] = v.strip()
            return out
    return {}


# ====================================================================== plural rules
def plural_function(expr: str):
    """C plural expression from a Plural-Forms header (e.g. ``n != 1``) -> function n -> index.
    A small recursive-descent parser (?: || && == != < > <= >= + - * / % ! and parentheses) –
    nothing from the file is ever passed to eval()."""
    toks = re.findall(r"\d+|n|&&|\|\||==|!=|<=|>=|[()?:<>+\-*/%!]|\S", expr.strip().rstrip(";"))
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take(t=None):
        nonlocal pos
        tok = peek()
        if tok is None or (t is not None and tok != t):
            raise ValueError(f"invalid plural expression: {expr!r}")
        pos += 1
        return tok

    def binary(next_level, ops):
        def parse():
            left = next_level()
            while peek() in ops:
                op = take()
                right = next_level()
                left = (lambda f, l, r: (lambda n: f(l(n), r(n))))(ops[op], left, right)
            return left
        return parse

    def unary():
        t = peek()
        if t == "!":
            take()
            v = unary()
            return lambda n: int(not v(n))
        if t == "-":
            take()
            v = unary()
            return lambda n: -v(n)
        if t == "(":
            take()
            v = ternary()
            take(")")
            return v
        if t == "n":
            take()
            return lambda n: n
        if t is not None and t.isdigit():
            take()
            c = int(t)
            return lambda n: c
        raise ValueError(f"invalid plural expression: {expr!r}")

    mul = binary(unary, {"*": lambda a, b: a * b, "/": lambda a, b: a // b if b else 0,
                         "%": lambda a, b: a % b if b else 0})
    add = binary(mul, {"+": lambda a, b: a + b, "-": lambda a, b: a - b})
    rel = binary(add, {"<": lambda a, b: int(a < b), ">": lambda a, b: int(a > b),
                       "<=": lambda a, b: int(a <= b), ">=": lambda a, b: int(a >= b)})
    eq = binary(rel, {"==": lambda a, b: int(a == b), "!=": lambda a, b: int(a != b)})
    land = binary(eq, {"&&": lambda a, b: int(bool(a) and bool(b))})
    lor = binary(land, {"||": lambda a, b: int(bool(a) or bool(b))})

    def ternary():
        cond = lor()
        if peek() == "?":
            take()
            a = ternary()
            take(":")
            b = ternary()
            return lambda n: a(n) if cond(n) else b(n)
        return cond

    fn = ternary()
    if pos != len(toks):
        raise ValueError(f"invalid plural expression: {expr!r}")
    return lambda n: int(fn(int(n)))


def _germanic(n: int) -> int:
    return 0 if n == 1 else 1


# ====================================================================== catalogs
class Catalog:
    def __init__(self, lang: str):
        self.lang = lang
        self.messages: dict[tuple[str | None, str], list[str]] = {}
        self.plural = _germanic
        self.nplurals = 2
        self.errors: list[str] = []
        self.files: list[Path] = []
        d = LOCALE_DIR / lang
        if lang == SOURCE_LANGUAGE or not d.is_dir():
            return
        for f in sorted(d.glob("*.po")):
            try:
                entries = parse_po(f.read_text("utf-8"), f.name)
            except (OSError, UnicodeDecodeError, POError) as e:
                self.errors.append(f"{f.name}: {e}")
                continue
            self.files.append(f)
            hdr = parse_header(entries)
            pf = hdr.get("Plural-Forms", "")
            m = re.search(r"nplurals\s*=\s*(\d+)\s*;\s*plural\s*=\s*([^;]+)", pf)
            if m:
                try:
                    self.plural = plural_function(m.group(2))
                    self.nplurals = int(m.group(1))
                except ValueError as e:
                    self.errors.append(f"{f.name}: {e}")
            for e in entries:
                if e.msgid == "" or e.fuzzy or not e.translated:
                    continue
                self.messages.setdefault(e.key, e.msgstr)      # first file wins on duplicates

    def get(self, ctx: str | None, msgid: str) -> str | None:
        v = self.messages.get((ctx, msgid))
        return v[0] if v else None

    def nget(self, ctx: str | None, singular: str, plural: str, n: int) -> str:
        v = self.messages.get((ctx, singular))
        if v and len(v) > 1:
            try:
                i = self.plural(n)
            except Exception:  # noqa: BLE001 – broken rule: English rule
                i = _germanic(n)
            if 0 <= i < len(v) and v[i]:
                return v[i]
        return singular if n == 1 else plural


def catalog(lang: str | None = None) -> Catalog:
    lang = lang or current_language()
    with _lock:
        c = _catalogs.get(lang)
        if c is None:
            c = _catalogs[lang] = Catalog(lang)
        return c


def reload() -> None:
    """forget cached catalogs (tests, translators editing a .po file)"""
    with _lock:
        _catalogs.clear()


# ====================================================================== language selection
def available_languages() -> list[str]:
    """language codes with a translation, the source language first"""
    out = [SOURCE_LANGUAGE]
    if LOCALE_DIR.is_dir():
        for d in sorted(LOCALE_DIR.iterdir()):
            if d.is_dir() and d.name != SOURCE_LANGUAGE and any(d.glob("*.po")):
                out.append(d.name)
    return out


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def normalize(code: str | None) -> str:
    """'de_DE.UTF-8' -> 'de', 'C' -> 'en', '' -> ''"""
    c = (code or "").strip()
    if not c:
        return ""
    if c.lower() == SYSTEM:
        return SYSTEM
    c = c.split(".")[0].split("@")[0].replace("-", "_")
    if c in ("C", "POSIX"):
        return SOURCE_LANGUAGE
    return c.split("_")[0].lower()


def system_language(env=None) -> str:
    """language of the system locale if a translation exists, else English"""
    env = os.environ if env is None else env
    avail = available_languages()
    cands = [x for x in (env.get("LANGUAGE") or "").split(":") if x]
    cands += [env.get(k, "") for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    for c in cands:
        n = normalize(c)
        if n and n != SYSTEM:
            return n if n in avail else SOURCE_LANGUAGE
    return SOURCE_LANGUAGE


def resolve(choice: str | None, env=None) -> str:
    """setting value ("system", "de", "en", …) -> language code that will be used"""
    n = normalize(choice)
    if not n or n == SYSTEM:
        return system_language(env)
    return n if n in available_languages() else SOURCE_LANGUAGE


def _configured() -> str:
    try:
        from . import config
        return getattr(config.load(), "language", SYSTEM) or SYSTEM
    except Exception:  # noqa: BLE001 – translation must never break the start
        return SYSTEM


def set_language(lang: str | None) -> str:
    """choose the language for this process ("system", a code, or None = env/config again) -> resolved code.
    Strings already translated (module constants) keep their language – set it before importing the UI."""
    global _lang
    with _lock:
        if lang is None:
            _lang = None
            return current_language()
        _lang = resolve(lang)
        return _lang


def current_language() -> str:
    global _lang
    if _lang is None:
        with _lock:
            if _lang is None:
                env = os.environ.get("FM1C_LANG", "").strip()
                _lang = resolve(env if env else _configured())
    return _lang


# ====================================================================== translation functions
def gettext(message: str) -> str:
    if current_language() == SOURCE_LANGUAGE:
        return message
    t = catalog().get(None, message)
    return message if t is None else t


def pgettext(context: str, message: str) -> str:
    if current_language() == SOURCE_LANGUAGE:
        return message
    t = catalog().get(context, message)
    return message if t is None else t


def ngettext(singular: str, plural: str, n: int) -> str:
    if current_language() == SOURCE_LANGUAGE:
        return singular if n == 1 else plural
    return catalog().nget(None, singular, plural, n)


def npgettext(context: str, singular: str, plural: str, n: int) -> str:
    if current_language() == SOURCE_LANGUAGE:
        return singular if n == 1 else plural
    return catalog().nget(context, singular, plural, n)


# languages that write a decimal comma (2,5 GB) – everything else uses a point
DECIMAL_COMMA = {"de", "fr", "es", "it", "nl", "pt", "pl", "cs", "da", "sv", "nb", "fi", "ru", "tr", "uk"}


def decimal(value: float, digits: int = 0) -> str:
    """number with the decimal separator of the UI language: decimal(2.5, 1) -> "2,5" (de) / "2.5" (en)"""
    s = f"{value:.{digits}f}"
    return s.replace(".", ",") if current_language() in DECIMAL_COMMA else s


def N_(message: str) -> str:
    """mark a string for extraction without translating it (translate later with _(value))"""
    return message


def NP_(singular: str, plural: str) -> tuple[str, str]:
    """mark a plural pair for extraction without translating it (later: ngettext(*value, n))"""
    return singular, plural


_ = gettext

__all__ = ["_", "N_", "NP_", "gettext", "ngettext", "pgettext", "npgettext", "set_language", "current_language",
           "available_languages", "language_name", "decimal", "resolve", "system_language", "reload", "parse_po"]
