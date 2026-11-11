#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Catalog audit, step 1: scan shallow clones of the catalog's firmwares for what the app needs to know (docs/firmware-support.md).

    python3 tools/firmware_audit.py <folder of clones, one per repo: owner_repo> <out.json>

Per firmware: the editor header (7D 46 4C), the INFO string of case ED_INFO (with its #define resolved), the backup
commands (Felucca ED_BACKUP_LIST 65.., SLOOP ED_BK_LIST 34..), ED_BK_IDS, X0X's image backup, the package identity
(tools/build.py PRODUCT and its --release formula), the web editor's BACKUP_IDS and what its docs say about V15.
Read the clones as data only (run with python3 -I); nothing in them is executed."""
import json, os, re, sys
from pathlib import Path

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])
SKIP_DIRS = {".git", "node_modules", "third_party", "vendor", "external", "CMSIS", "sdk", "lib", "build", "dist"}
CODE_EXT = {".c", ".h", ".cpp", ".hpp", ".cc"}


def files(root, exts):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for f in fns:
            if os.path.splitext(f)[1].lower() in exts:
                p = Path(dp) / f
                try:
                    if p.stat().st_size < (700_000 if p.suffix in (".c", ".h") else 400_000):
                        yield p
                except OSError:
                    pass


def read(p):
    try:
        return p.read_text("latin-1")
    except OSError:
        return ""


def strip_comments(t):
    t = re.sub(r"/\*.*?\*/", " ", t, flags=re.S)
    return re.sub(r"//[^\n]*", " ", t)


def resolve(expr, defs, depth=0):
    out = ""
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|([A-Za-z_]\w*)', expr):
        if m.group(1) is not None:
            out += m.group(1)
        elif depth < 6 and m.group(2) in defs:
            out += resolve(defs[m.group(2)], defs, depth + 1)
        elif m.group(2) not in ("sizeof", "u8", "char", "const"):
            out += "{" + m.group(2) + "}"
    return out


def enum_values(text):
    vals = {}
    for m in re.finditer(r"enum\s*\w*\s*\{([^}]*)\}", text):
        n = -1
        for item in m.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            mm = re.match(r"([A-Za-z_]\w*)\s*(?:=\s*(0x[0-9a-fA-F]+|\d+|[A-Za-z_]\w*))?", item)
            if not mm:
                continue
            if mm.group(2):
                g = mm.group(2)
                n = int(g, 0) if g[0].isdigit() else vals.get(g, n)
            else:
                n += 1
            vals[mm.group(1)] = n
    return vals


def scan(d: Path):
    r = {"dir": d.name}
    code = {p: strip_comments(read(p)) for p in files(d, CODE_EXT)}
    raw = {p: read(p) for p in code}
    r["n_c"] = len(code)
    defs = {}
    for t in code.values():
        for m in re.finditer(r'^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+([^\n]*)$', t, re.M):
            v = m.group(2).strip()
            if v.startswith("\"") or re.fullmatch(r"[A-Za-z_]\w*(?:\s+(?:\"[^\"]*\"|[A-Za-z_]\w*))*", v): defs.setdefault(m.group(1), v)
    # Felucca editor header
    hdr = set()
    for p, t in code.items():
        m0 = re.search(r"#\s*define\s+(\w*?)H(?:DR)?0\w*\s+0x([0-9a-fA-F]{2})", t)
        for m in re.finditer(r"#\s*define\s+(\w+?)(?:HDR|H)0u?\s+0x([0-9a-fA-F]{2})u?", t):
            pre = m.group(1)
            m1 = re.search(r"#\s*define\s+" + re.escape(pre) + r"(?:HDR|H)1u?\s+0x([0-9a-fA-F]{2})", t)
            m2 = re.search(r"#\s*define\s+" + re.escape(pre) + r"(?:HDR|H)2u?\s+0x([0-9a-fA-F]{2})", t)
            if m1 and m2:
                hdr.add(f"{m.group(2)} {m1.group(1)} {m2.group(1)}".upper())
        for m in re.finditer(r"0x7[dD]u?,\s*0x([0-9a-fA-F]{2})u?,\s*0x([0-9a-fA-F]{2})u?", t):
            hdr.add(f"7D {m.group(1)} {m.group(2)}".upper())
    r["headers"] = sorted(hdr)
    # INFO strings
    infos = set()
    enums = {}
    for p, t in code.items():
        ev = enum_values(t)
        if "ED_INFO" in ev or any(k.startswith("ED_") for k in ev):
            for k, v in ev.items():
                enums.setdefault(k, v)
        for m in re.finditer(r"case\s+ED_INFO\s*:", t):
            seg = t[m.end():m.end() + 600]
            mm = re.search(r"\w*str\w*\s*\(([^,;]*),", seg)
            if mm:
                infos.add(resolve(mm.group(1), defs))
    r["info"] = sorted(infos)
    for k in ("FELUCCA_VERSION", "ANN_NAME", "FW_VERSION", "VERSION_STRING", "FIRMWARE_NAME"):
        if k in defs:
            r.setdefault("defs", {})[k] = resolve(defs[k], defs)
    # backup commands
    bk = {}
    for k in ("ED_BACKUP_LIST", "ED_BACKUP_GET", "ED_BACKUP_PUT", "ED_BK_LIST", "ED_BK_GET", "ED_BK_PUT", "ED_MOTION"):
        if k in enums:
            bk[k] = enums[k]
    r["bk_cmds"] = bk
    r["ed_cmds"] = len([k for k in enums if k.startswith("ED_")])
    ids = {}
    for p, t in code.items():
        for m in re.finditer(r"\b([A-Z_]*(?:BK|BACKUP)[A-Z_0-9]*IDS?[A-Z_0-9]*)\s*\[\s*\w*\s*\]\s*=\s*\{([^}]*)\}", t):
            body = m.group(2)
            nums = []
            ok = True
            for x in body.split(","):
                x = x.strip()
                if not x:
                    continue
                try:
                    nums.append(int(x.rstrip("uU"), 0))
                except ValueError:
                    ok = False
            ids[m.group(1) + "@" + p.name] = nums if ok else body.strip()[:200]
    r["bk_ids"] = ids
    # X0X image backup
    r["x0x_image"] = any(re.search(r"BU_MAGIC|RS_REQ\s+0x41", t) for t in code.values())
    r["fomni_announce"] = any("ANN_H1 0x53" in t.replace("u\n", "\n") or re.search(r"ANN_H1\s+0x53", t) for t in code.values())
    # identity
    prods = set()
    py = list(files(d, {".py", ".sh", ".mk", ".json", ".js", ".toml", ".cmake", ".txt", ".bat"})) + \
        [p for p in d.rglob("Makefile*") if ".git" not in p.parts][:10]
    for p in py:
        t = read(p)
        for m in re.finditer(r'PRODUCT\w*\s*[:=]\s*[\'"]([^\'"]+)[\'"]', t):
            prods.add(m.group(1))
        for m in re.finditer(r'(?:FM-1_9|FM-1_)["\']\s*(?:\+|%)', t):
            prods.add("formula:" + t[m.start():m.start() + 60].split("\n")[0])
    for t in code.values():
        for m in re.finditer(r'"(FM-1_\d+)"', t):
            prods.add(m.group(1))
    r["product"] = sorted(prods)[:12]
    # base
    names = {p.name for p in code}
    r["has_felucca_c"] = "felucca.c" in names
    r["has_editor_c"] = "editor.c" in names
    r["fm1base"] = (d / "base").is_dir() or (d / "firmware.json").exists()
    r["mvave_sdk"] = (d / "sdk").is_dir() or any("FM1_COMMUNITY" in t or "FM1_UPDATE_PRODUCT" in t for t in raw.values())
    # web editor backup ids
    for p in files(d, {".js", ".html"}):
        t = read(p)
        for m in re.finditer(r"\b([A-Z_]*BACKUP_IDS|[A-Z_]*BK_IDS)\s*=\s*\[([^\]]*)\]", t):
            r.setdefault("web_ids", {})[m.group(1) + "@" + p.name] = m.group(2).strip()[:200]
        if '"sloop-backup"' in t or "'sloop-backup'" in t:
            r["web_sloop_backup"] = True
        if '"felucca-backup"' in t or "'felucca-backup'" in t:
            r["web_felucca_backup"] = True
    # docs: V15 requirement
    v15 = []
    for p in list(d.glob("*.md")) + list((d / "docs").glob("*.md")) if (d / "docs").is_dir() else list(d.glob("*.md")):
        t = read(p)
        for m in re.finditer(r"[^\n]{0,90}(?:\bV ?1\.?5\b|stock 15|v15|V15|FM-1_015|firmware 1\.5)[^\n]{0,90}", t, re.I):
            v15.append(p.name + ": " + m.group(0).strip()[:200])
    r["v15_docs"] = v15[:4]
    return r


def one(d):
    try:
        return scan(d)
    except Exception as e:  # noqa
        return {"dir": d.name, "error": repr(e)}
import multiprocessing as mp
with mp.Pool(8) as pool:
    res = pool.map(one, [d for d in sorted(SRC.iterdir()) if d.is_dir()], chunksize=1)
OUT.write_text(json.dumps(res, indent=1))
print(len(res))
