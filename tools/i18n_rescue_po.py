#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Write fm1companion/locale/de/rescue.po from the table in flash/texts.py: every msgstr is the original German
template of the pinned rescue files. Run after changing the table (then tools/i18n_extract.py).

  python3 tools/i18n_rescue_po.py
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import i18n_extract as X
from fm1companion.flash import texts as T
de = {}
def put(en, g):
    if en in de and de[en] != g:
        raise SystemExit(f"conflict {en!r}: {de[en]!r} vs {g!r}")
    de[en] = g
for r in T.RULES:
    put(r.en if isinstance(r.en, str) else r.en[0], r.de)
    for k in r.kind.values():
        if isinstance(k, dict):
            for g, en in k.items(): put(en, g)
put("{name} {size} bytes", "{name} {size} Bytes")
msgs = [m for m in X.extract() if any(ref.startswith("fm1companion/flash/texts.py") for ref in m.refs)]
missing = [m.msgid for m in msgs if m.msgid not in de]
if missing: raise SystemExit(f"no German for {missing}")
head = '''# German translation of FM-1 Companion – rescue texts: messages of the pinned recovery tools, shown by flash/texts.py.
# The msgids come from flash/texts.py; the pinned files are flash/flashmodel.py, flash/restzone.py, flash/rescuegate.py.
# Each msgstr is the ORIGINAL German template of the pinned file, character for character – the German UI shows
# exactly what those files write. Do not reword.
# SPDX-License-Identifier: GPL-3.0-only
msgid ""
msgstr ""
"Project-Id-Version: FM-1 Companion\\n"
"Language: de\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"
'''
out = [head]
for m in msgs:
    g = de[m.msgid]
    m.refs = [r for r in m.refs if r.startswith("fm1companion/flash/texts.py")]
    out.append(X.format_entry(m, [g, g] if m.msgid_plural else [g]))
open(ROOT / "fm1companion/locale/de/rescue.po", "w", encoding="utf-8").write("\n".join(out))
print(len(msgs), "entries")
