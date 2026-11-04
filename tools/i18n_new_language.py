#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Start a new translation: create fm1companion/locale/<lang>/ with one empty .po file per area.

  python3 tools/i18n_new_language.py fr                 # French
  python3 tools/i18n_new_language.py pt_BR --name "Português (Brasil)" --plural "nplurals=2; plural=(n > 1);"

The areas (frame, firmware, music, …) and their messages are the same as in the German catalogs
(fm1companion/locale/de/*.po); the msgids, source references and translator hints come from the current template
messages.pot, every msgstr is empty. A README.md with the next steps is added. Nothing else is changed: the app finds
the new folder by itself (i18n.available_languages() lists every folder with .po files), untranslated entries show
the English text. An existing folder is never overwritten.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fm1companion import i18n  # noqa: E402
import i18n_extract  # noqa: E402

LOCALE = ROOT / "fm1companion" / "locale"
REFERENCE = "de"                     # the complete translation whose files define the areas
CODE_RE = re.compile(r"^[a-z]{2,3}(?:_[A-Z]{2}|_[A-Z][a-z]{3})?$")

HEADER = '''# Translation of FM-1 Companion into {name} ({code}) – {area}
# SPDX-License-Identifier: GPL-3.0-only
msgid ""
msgstr ""
"Project-Id-Version: FM-1 Companion\\n"
"Language: {code}\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: {plural}\\n"
'''

README = '''# {name} ({code})

Translation of FM-1 Companion into {name}, started with `tools/i18n_new_language.py {code}`.

Each `.po` file is one area of the app (the same split as `../de/`). Every `msgstr` is still empty – the app shows
the English text for those. Translate with any gettext editor (Poedit, Lokalize, GNOME Translation Editor) or a text
editor:

1. Check the header of every file: `Plural-Forms` must match {name}
   (see https://www.gnu.org/software/gettext/manual/html_node/Plural-forms.html).
2. Translate the `msgstr` entries. Keep placeholders such as `{{name}}` and `%s` exactly, keep HTML tags,
   choose your own accelerator letter after `&`.
3. Check and look at it:

   ```
   python3 tools/i18n_check.py --lang {code}
   FM1C_LANG={code} python3 -m fm1companion --demo
   ```

4. Optional: add the name of the language in its own language to `LANGUAGE_NAMES` in `fm1companion/i18n.py`
   (otherwise the language list shows the code `{code}`), and translate the help pages
   (`fm1companion/data/help/en/*.md` → `fm1companion/data/help/{code}/`).

The entries in `rescue.po` (used by `flash/texts.py`) are safety messages of the recovery tools – translate them
with the same meaning and strength, nothing softer, nothing stronger.

More: `docs/TRANSLATING.md`.
'''


def _area(path: Path) -> str:
    """area description from the first comment line of the reference file"""
    first = path.read_text("utf-8").splitlines()[0] if path.stat().st_size else ""
    return first.split("–", 1)[1].strip() if "–" in first else path.stem


def build(code: str, name: str = "", plural: str = "nplurals=2; plural=(n != 1);",
          locale: Path = LOCALE) -> list[Path]:
    """create locale/<code>/ -> list of the files written. Raises ValueError for a bad or existing language."""
    if not CODE_RE.match(code):
        raise ValueError(f"not a language code: {code!r} (examples: fr, pt_BR)")
    if code in (i18n.SOURCE_LANGUAGE, REFERENCE):
        raise ValueError(f"{code} exists already")
    target = locale / code
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"{target} exists already – nothing changed")
    name = name or i18n.LANGUAGE_NAMES.get(code, code)
    pot = {(m.msgctxt, m.msgid): m for m in i18n_extract.extract()}
    used: set = set()
    files: dict[str, list] = {}
    areas: dict[str, str] = {}
    for f in sorted((locale / REFERENCE).glob("*.po")):
        entries = i18n.parse_po(f.read_text("utf-8"), f.name)
        msgs = []
        for e in entries:
            if e.msgid == "" and e.msgctxt is None:
                continue
            m = pot.get(e.key)
            if m is not None and e.key not in used:
                used.add(e.key)
                msgs.append(m)
        files[f.name] = msgs
        areas[f.name] = _area(f)
    rest = [m for k, m in pot.items() if k not in used]
    if rest:
        files["other.po"] = rest
        areas["other.po"] = "messages not yet sorted into an area"
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for fname, msgs in files.items():
        text = HEADER.format(name=name, area=areas[fname], code=code, plural=plural)
        text += "".join("\n" + i18n_extract.format_entry(m) for m in msgs)
        (target / fname).write_text(text, "utf-8")
        written.append(target / fname)
    (target / "README.md").write_text(README.format(name=name, code=code), "utf-8")
    written.append(target / "README.md")
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("lang", help="language code, e.g. fr, es, pt_BR")
    ap.add_argument("--name", default="", help="name of the language (for the file headers), e.g. Français")
    ap.add_argument("--plural", default="nplurals=2; plural=(n != 1);",
                    help="Plural-Forms of the language (default: like English/German)")
    a = ap.parse_args(argv)
    try:
        i18n.plural_function(a.plural.split("plural=", 1)[-1])
        files = build(a.lang, a.name, a.plural)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    n = sum(len(i18n.parse_po(p.read_text("utf-8"))) - 1 for p in files if p.suffix == ".po")
    print(f"created {files[0].parent.relative_to(ROOT)}: {len(files) - 1} .po files, {n} messages to translate, README.md")
    print(f"next: translate the msgstr entries, then  python3 tools/i18n_check.py --lang {a.lang}  and  "
          f"FM1C_LANG={a.lang} python3 -m fm1companion --demo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
