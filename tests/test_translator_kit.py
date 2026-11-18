# SPDX-License-Identifier: GPL-3.0-only
"""Translator kit: tools/i18n_new_language.py starts a language from the template, the app finds it by itself."""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from fm1companion import i18n  # noqa: E402
import i18n_extract  # noqa: E402
import i18n_new_language as nl  # noqa: E402


@pytest.fixture
def locale(tmp_path, monkeypatch):
    loc = tmp_path / "locale"
    shutil.copytree(i18n.LOCALE_DIR / "de", loc / "de")
    shutil.copytree(i18n.LOCALE_DIR / "en", loc / "en")
    monkeypatch.setattr(i18n, "LOCALE_DIR", loc)
    i18n.reload()
    yield loc
    i18n.reload()


def test_new_language_has_every_message_empty(locale):
    files = nl.build("fr", "Français", "nplurals=2; plural=(n > 1);", locale)
    names = sorted(p.name for p in files)
    assert names == sorted([p.name for p in (locale / "de").glob("*.po")] + ["README.md"])
    pot = {(m.msgctxt, m.msgid) for m in i18n_extract.extract()}
    seen = set()
    for f in files:
        if f.suffix != ".po":
            continue
        entries = i18n.parse_po(f.read_text("utf-8"), f.name)
        hdr = i18n.parse_header(entries)
        assert hdr["Language"] == "fr" and hdr["Plural-Forms"] == "nplurals=2; plural=(n > 1);"
        for e in entries[1:]:
            assert not e.translated and e.key not in seen
            seen.add(e.key)
    assert seen == pot
    assert "FM1C_LANG=fr" in (locale / "fr" / "README.md").read_text("utf-8")


def test_available_languages_detects_folders_with_po_files(locale):
    assert i18n.available_languages() == ["en", "de"]
    (locale / "xx").mkdir()
    (locale / "xx" / "notes.txt").write_text("no catalog here")
    assert "xx" not in i18n.available_languages()                  # a folder without .po files is no language
    nl.build("fr", locale=locale)
    assert i18n.available_languages() == ["en", "de", "fr"]
    assert i18n.resolve("fr") == "fr" and i18n.language_name("fr") == "fr"
    cat = i18n.catalog("fr")
    assert not cat.errors and not cat.messages                      # all empty: the English text is shown
    i18n.set_language("fr")
    try:
        assert i18n._("Cancel") == "Cancel"
        assert i18n.ngettext("{n} file", "{n} files", 0) == "{n} files"
    finally:
        i18n.set_language("de")


def test_refuses_existing_or_bad_codes(locale):
    for bad in ("de", "en", "../x", "FR", ""):
        with pytest.raises(ValueError):
            nl.build(bad, locale=locale)
    nl.build("es", locale=locale)
    with pytest.raises(ValueError):
        nl.build("es", locale=locale)


def test_language_names_itself_in_the_po_header(locale):
    """I18N-05: Settings › Language showed Spanish as "es". The name is the header line X-Language-Name of the
    language's own .po files, which the new-language tool writes – no table in the code to forget."""
    assert i18n.language_name("en") == "English" and i18n.language_name("de") == "Deutsch"
    nl.build("fr", "Français", locale=locale)
    assert i18n.language_name("fr") == "Français"
    nl.build("pt_BR", "Português (Brasil)", locale=locale)
    assert i18n.language_name("pt_BR") == "Português (Brasil)"
    nl.build("it", locale=locale)                                   # no name given: the code, as before
    assert i18n.language_name("it") == "it"
    assert i18n.language_name("xx") == "xx"                         # no such folder


def test_shipped_languages_have_their_names():
    names = {c: i18n.language_name(c) for c in i18n.available_languages()}
    assert names["en"] == "English" and names["de"] == "Deutsch" and names["es"] == "Español"
    assert all(n != c for c, n in names.items()), names             # none shows up as a bare code
    for c in names:
        if c != "en":
            for f in (i18n.LOCALE_DIR / c).glob("*.po"):
                assert i18n.parse_header(i18n.parse_po(f.read_text("utf-8"), f.name)).get("X-Language-Name") == names[c], f


def test_help_lists_every_language():
    import os
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "FM1C_LANG"}
    out = subprocess.run([sys.executable, "-m", "fm1companion", "--lang", "en", "--help"], cwd=str(ROOT), env=env,
                         capture_output=True, text=True).stdout
    assert ": " + ", ".join(i18n.available_languages()) + " (" in out, out
