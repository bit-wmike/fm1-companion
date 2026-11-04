# SPDX-License-Identifier: GPL-3.0-only
"""English help pages: one for every German page, same links, and every app:// / help: link leads somewhere
(same check as in ui_frame_test.py, but without building the window: the page keys are read from the sources)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HELP = ROOT / "fm1companion" / "data" / "help"
UI = ROOT / "fm1companion" / "ui"
LINK = re.compile(r"\]\((app://page/[^)]+|app://action/[^)]+|help:[^)]+)\)")


def page_keys() -> set[str]:
    """every key window.page() knows: built-in pages, absorbed tabs and the plug-in pages in ui/pages/"""
    src = (UI / "main_window.py").read_text("utf-8")
    m = re.search(r"^PAGES = \[(.*?)\]\n", src, re.S | re.M)
    keys = set(re.findall(r"\(\"(\w+)\", _\(", m.group(1)))
    keys |= set(re.findall(r"PageSpec\(\"(\w+)\"", src))
    keys |= set(re.findall(r"\(\"\w+\", _\(\"[^\"]+\"\), \"(\w+)\"\)", src))     # tabs of composite pages
    for f in (UI / "pages").glob("*.py"):
        keys |= set(re.findall(r"PageSpec\(key=\"(\w+)\"", f.read_text("utf-8")))
    return keys


def test_page_keys_found():
    keys = page_keys()
    assert {"overview", "firmware", "discover", "recovery", "backups", "studio", "workshop", "simulator",
            "project_view", "backup_browser", "fm1"} <= keys


def test_every_german_page_has_an_english_one():
    de = sorted(f.name for f in (HELP / "de").glob("*.md"))
    en = sorted(f.name for f in (HELP / "en").glob("*.md"))
    assert de and en == de


def test_english_links_are_valid_and_match_german():
    from fm1companion.ui.pages.help import ACTIONS
    keys = page_keys()
    topics = {f.stem for f in (HELP / "de").glob("*.md")}
    bad = []
    for f in sorted((HELP / "en").glob("*.md")):
        md = f.read_text("utf-8")
        assert re.match(r"#\s+\S", md), f"{f.name}: no title"
        links = LINK.findall(md)
        for link in links:
            if link.startswith("app://page/") and link[len("app://page/"):].strip("/") not in keys:
                bad.append(f"{f.name}: {link}")
            if link.startswith("app://action/") and link[len("app://action/"):].strip("/") not in ACTIONS:
                bad.append(f"{f.name}: {link}")
            if link.startswith("help:") and link[5:] not in topics:
                bad.append(f"{f.name}: {link}")
        de_links = LINK.findall((HELP / "de" / f.name).read_text("utf-8"))
        if sorted(links) != sorted(de_links):
            bad.append(f"{f.name}: links differ from the German page")
    assert not bad, bad


def test_german_links_are_valid():
    """the German pages too (ui_frame_test checks them in the running window)"""
    from fm1companion.ui.pages.help import ACTIONS
    keys = page_keys()
    topics = {f.stem for f in (HELP / "de").glob("*.md")}
    bad = [f"{f.name}: {link}" for f in (HELP / "de").glob("*.md") for link in LINK.findall(f.read_text("utf-8"))
           if (link.startswith("app://page/") and link[11:].strip("/") not in keys)
           or (link.startswith("app://action/") and link[13:].strip("/") not in ACTIONS)
           or (link.startswith("help:") and link[5:] not in topics)]
    assert not bad, bad


def test_english_help_is_english():
    """titles in English and no German UI words left over"""
    from fm1companion.ui.pages import help as help_page
    titles = {k: t for k, t, _m in help_page.topics("en")}
    de_titles = {k: t for k, t, _m in help_page.topics("de")}
    same = {"04-backups", "05-samples-kits", "07-simulator", "13-studio", "14-melodee-1-6-0"}   # same word in both
    assert [k for k in titles if (titles[k] == de_titles[k]) != (k in same)] == [], titles
    words = re.compile(r"\b(und|oder|nicht|Gerät|Einstellungen|Rettung|Stufe|Installieren|Zurück|Seite)\b")
    left = [f"{f.name}: {m.group(0)}" for f in (HELP / "en").glob("*.md") for m in words.finditer(f.read_text("utf-8"))]
    assert not left, left
