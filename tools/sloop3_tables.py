#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Write fm1companion/data/sloop3_tables.json from a SLOOP 3 checkout (isod89/sloop3-fm1, GPL-3.0): the names the
project view shows for a SLOOP 3 project – its engines (engine/engines.c, in that order), its factory sounds
(app/preset_data.c: name, engine, kind, in that order = the project's preset number) and its drum kits
(engine/drumkits.c gb_dkit_names).

    python3 tools/sloop3_tables.py /path/to/sloop3-fm1 [--tag v3.4.0-beta --commit 2228313]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "fm1companion" / "data" / "sloop3_tables.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--tag", default="")
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    src = Path(a.src)
    eng_list = re.search(r"gb_engines\[\]\s*=\s*\{([^}]*)\}", (src / "engine/engines.c").read_text()).group(1)
    names = {}
    for f in (src / "engine").glob("*.c"):
        for m in re.finditer(r"const gb_engine (gb_eng_\w+)\s*=\s*\{\s*\"([^\"]+)\"", f.read_text()):
            names[m.group(1)] = m.group(2)
    engines = [names[x.strip().lstrip("&")] for x in eng_list.split(",") if x.strip()]
    enum = re.search(r"enum \{ (ENG_VA[^}]*)\}", (src / "engine/engine.h").read_text()).group(1)
    eng_ids = {n.strip(): i for i, n in enumerate(enum.split(",")) if n.strip()}
    eng_ids.update(ENG_ANALOG=eng_ids["ENG_VA"], ENG_DIGITAL=eng_ids["ENG_FM"])
    pres = []
    kinds = ["K_BASSE", "K_KEYS", "K_ORGUE", "K_PAD", "K_LEAD", "K_PLUCK", "K_BRASS", "K_VOIX", "K_FX", "K_KIT"]
    for m in re.finditer(r'^\s*\{ "([^"]*)",\s*(ENG_\w+),\s*(K_\w+),', (src / "app/preset_data.c").read_text(), re.M):
        pres.append([m.group(1).strip(), eng_ids[m.group(2)], kinds.index(m.group(3))])
    kits = re.search(r"gb_dkit_names\[GB_NKITS\]\s*=\s*\{([^}]*)\}", (src / "engine/drumkits.c").read_text()).group(1)
    kind_names = re.search(r"gb_kind_name\[K_N\]\s*=\s*\{([^}]*)\}", (src / "app/preset_data.c").read_text()).group(1)
    out = {"source": "isod89/sloop3-fm1" + (f" {a.tag}" if a.tag else "") + (f" ({a.commit})" if a.commit else ""),
           "licence": "GPL-3.0-only",
           "engines": engines,
           "kinds": re.findall(r'"([^"]*)"', kind_names),
           "presets": pres,
           "kits": re.findall(r'"([^"]*)"', kits)}
    OUT.write_text(json.dumps(out, indent=0, ensure_ascii=False) + "\n", "utf-8")
    print(f"{OUT}: {len(engines)} engines, {len(pres)} sounds, {len(out['kits'])} kits")


if __name__ == "__main__":
    main()
