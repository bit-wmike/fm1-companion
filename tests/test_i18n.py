# SPDX-License-Identifier: GPL-3.0-only
"""Translations: .po parser, fallback, plurals, placeholder check, language choice, FM1C_LANG, tools."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from fm1companion import i18n  # noqa: E402

PO = r'''# a comment
msgid ""
msgstr ""
"Language: xx\n"
"Plural-Forms: nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);\n"

#: fm1companion/a.py:1
msgid "Save"
msgstr "Sichern"

msgctxt "verb"
msgid "Open"
msgstr "Aufmachen"

msgid "Open"
msgstr "Offen"

msgid ""
"Line one\n"
"line \"two\"\t{n}"
msgstr "Zeile eins\nZeile \"zwei\"\t{n}"

msgid "{n} file"
msgid_plural "{n} files"
msgstr[0] "{n} plik"
msgstr[1] "{n} pliki"
msgstr[2] "{n} plików"

#, fuzzy
msgid "Fuzzy"
msgstr "Unsicher"

msgid "Empty"
msgstr ""

#~ msgid "Old"
#~ msgstr "Alt"
'''


@pytest.fixture
def lang_env(tmp_path, monkeypatch):
    """private locale folder with languages 'xx' (two files) and 'de'; language state restored afterwards"""
    loc = tmp_path / "locale"
    (loc / "xx").mkdir(parents=True)
    (loc / "xx" / "a.po").write_text(PO, "utf-8")
    (loc / "xx" / "b.po").write_text('msgid ""\nmsgstr ""\n\nmsgid "Close"\nmsgstr "Zu"\n', "utf-8")
    (loc / "de").mkdir()
    (loc / "de" / "x.po").write_text('msgid "Save"\nmsgstr "Speichern"\n', "utf-8")
    monkeypatch.setattr(i18n, "LOCALE_DIR", loc)
    i18n.reload()
    yield loc
    i18n.set_language(None)
    i18n.reload()


def test_parser_entries():
    es = i18n.parse_po(PO, "a.po")
    by = {(e.msgctxt, e.msgid): e for e in es}
    assert by[(None, "Save")].msgstr == ["Sichern"] and by[(None, "Save")].references == ["fm1companion/a.py:1"]
    assert by[("verb", "Open")].msgstr == ["Aufmachen"] and by[(None, "Open")].msgstr == ["Offen"]
    assert by[(None, 'Line one\nline "two"\t{n}')].msgstr == ['Zeile eins\nZeile "zwei"\t{n}']
    p = by[(None, "{n} file")]
    assert p.msgid_plural == "{n} files" and p.msgstr == ["{n} plik", "{n} pliki", "{n} plików"]
    assert by[(None, "Fuzzy")].fuzzy and not by[(None, "Empty")].translated
    assert (None, "Old") not in by                              # obsolete entries are skipped
    assert i18n.parse_header(es)["Language"] == "xx"


@pytest.mark.parametrize("bad", ['msgid "a\nmsgstr "b"\n', 'msgstr "x"\n', 'msgid "a"\n', 'msgid "a" "b"\nmsgstr ""\n',
                                 'msgid "a\\q"\nmsgstr ""\n', 'msgid "a"\nmsgstr[0] "x"\nmsgid_plural "b"\n'])
def test_parser_errors(bad):
    with pytest.raises(i18n.POError):
        i18n.parse_po(bad, "bad.po")


def test_unescape_roundtrip():
    s = 'a "b" \\ c\nd\te'
    assert i18n.unescape(i18n.escape(s)) == s
    assert i18n.unescape(r"\101\x42") == "AB"


def test_plural_expressions():
    pl = i18n.plural_function("(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2)")
    assert [pl(n) for n in (1, 2, 4, 5, 12, 22, 25, 0)] == [0, 1, 1, 2, 2, 1, 2, 2]
    assert [i18n.plural_function("n != 1")(n) for n in (0, 1, 2)] == [1, 0, 1]
    assert i18n.plural_function("0")(7) == 0 and i18n.plural_function("n>1")(1) == 0
    for bad in ("n; import os", "__import__('os')", "n ? 1", "n +"):
        with pytest.raises(ValueError):
            i18n.plural_function(bad)


def test_translate_fallback_and_merge(lang_env):
    assert i18n.set_language("xx") == "xx"
    _ = i18n._
    assert _("Save") == "Sichern" and _("Close") == "Zu"                  # two .po files merged
    assert _("Not translated") == "Not translated"                          # missing -> English source
    assert _("Fuzzy") == "Fuzzy" and _("Empty") == "Empty"                  # fuzzy/empty ignored
    assert i18n.pgettext("verb", "Open") == "Aufmachen" and _("Open") == "Offen"
    assert i18n.pgettext("other", "Open") == "Open"
    assert _("Saved {n} files").format(n=3) == "Saved 3 files"
    assert i18n.N_("Save") == "Save"


def test_plural(lang_env):
    i18n.set_language("xx")
    ng = i18n.ngettext
    assert [ng("{n} file", "{n} files", n).format(n=n) for n in (1, 3, 5, 22)] == \
        ["1 plik", "3 pliki", "5 plików", "22 pliki"]
    assert ng("{n} dog", "{n} dogs", 1) == "{n} dog" and ng("{n} dog", "{n} dogs", 2) == "{n} dogs"
    i18n.set_language("en")
    assert ng("{n} file", "{n} files", 1) == "{n} file" and ng("{n} file", "{n} files", 0) == "{n} files"
    assert i18n.npgettext("c", "{n} file", "{n} files", 2) == "{n} files"


def test_language_choice(lang_env, monkeypatch):
    assert i18n.available_languages() == ["en", "de", "xx"]
    env = {"LANG": "de_DE.UTF-8"}
    assert i18n.system_language(env) == "de"
    assert i18n.system_language({"LANG": "fr_FR.UTF-8"}) == "en"           # no translation -> English
    assert i18n.system_language({"LANGUAGE": "xx:de", "LANG": "de_DE.UTF-8"}) == "xx"
    assert i18n.system_language({"LANG": "C"}) == "en" and i18n.system_language({}) == "en"
    assert i18n.resolve("system", env) == "de" and i18n.resolve("en", env) == "en"
    assert i18n.resolve("zz", env) == "en"                                  # unknown language -> English
    assert i18n.set_language("de") == "de" and i18n._("Save") == "Speichern"
    assert i18n.set_language("en") == "en" and i18n._("Save") == "Save"
    assert i18n.decimal(2.5, 1) == "2.5"
    i18n.set_language("de")
    assert i18n.decimal(2.5, 1) == "2,5"


def test_fm1c_lang_env(lang_env, monkeypatch):
    monkeypatch.setenv("FM1C_LANG", "xx")
    i18n.set_language(None)                     # back to env/config
    assert i18n.current_language() == "xx" and i18n._("Save") == "Sichern"
    monkeypatch.setenv("FM1C_LANG", "en")
    i18n.set_language(None)
    assert i18n.current_language() == "en"


def test_config_setting(lang_env, monkeypatch):
    from fm1companion import config
    monkeypatch.delenv("FM1C_LANG", raising=False)
    s = config.Settings()
    assert s.language == "system"
    s.language = "xx"
    monkeypatch.setattr(config, "load", lambda: s)
    i18n.set_language(None)
    assert i18n.current_language() == "xx"
    s2, probs = config.from_dict({"language": 5})
    assert s2.language == "system" and probs


def test_tests_run_in_german():
    """conftest sets FM1C_LANG=de: the existing German assertions keep working"""
    assert os.environ.get("FM1C_LANG") == "de"
    i18n.set_language(None)
    i18n.reload()
    assert i18n.current_language() == "de"
    assert i18n._("Overview") == "Übersicht"
    from fm1companion.ui.pages import GROUP_TITLES
    assert GROUP_TITLES["geraet"] == "Gerät"


def test_real_catalog_and_placeholder_check():
    import i18n_check
    from i18n_extract import extract
    assert i18n_check.placeholders("Saved {n} files to {path!r} %s %(x)d %%") == \
        {"{n}": 1, "{path}": 1, "%s": 1, "%(x)d": 1}
    e = i18n.POEntry("Saved {n} files", ["{n} Dateien in {ort}"])
    assert any("unknown" in p for p in i18n_check.check_placeholders(e))
    e = i18n.POEntry("Saved {n} files to {path}", ["Gespeichert"])
    assert any("lacks" in p for p in i18n_check.check_placeholders(e))
    e = i18n.POEntry("{n} file", ["eine Datei", "{n} Dateien"], msgid_plural="{n} files")
    assert i18n_check.check_placeholders(e) == []               # a plural form may drop {n}
    known = {(m.msgctxt, m.msgid) for m in extract()}
    for lang in i18n_check.languages():
        errors, st = i18n_check.check_language(lang, known)
        assert errors == [], errors
        assert not st["obsolete"], st["obsolete"][:5]
    errors, st = i18n_check.check_language("de", known)
    assert not st["missing"] or len(st["missing"]) < len(known)


def test_duplicate_msgids_across_files(tmp_path, monkeypatch):
    import i18n_check
    loc = tmp_path / "locale"
    (loc / "de").mkdir(parents=True)
    (loc / "de" / "a.po").write_text('msgid "Save"\nmsgstr "Speichern"\n', "utf-8")
    (loc / "de" / "b.po").write_text('msgid "Save"\nmsgstr "Sichern"\n', "utf-8")
    monkeypatch.setattr(i18n_check, "LOCALE", loc)
    monkeypatch.setattr(i18n_check, "ROOT", tmp_path)
    errors, _st = i18n_check.check_language("de", {(None, "Save")})
    assert len(errors) == 1 and "different msgstr" in errors[0]


def test_extractor(tmp_path):
    from i18n_extract import extract_source, format_entry, Message
    src = '''from x import _, N_, ngettext, pgettext
A = N_("Marked")
def f(n, name):
    # Translators: the button that saves
    a = _("Save")
    b = _(f"Hi {name}")
    c = ngettext("{n} file", "{n} files", n)
    d = pgettext("verb", "Open")
    e = _("Multi " "part")
'''
    warn = []
    found = extract_source(src, "m.py", warn)
    ids = [(c, m, p) for c, m, p, _l, _c in found]
    assert (None, "Marked", None) in ids and (None, "Save", None) in ids and ("verb", "Open", None) in ids
    assert (None, "{n} file", "{n} files") in ids and (None, "Multi part", None) in ids
    assert len(warn) == 1 and "f-string" in warn[0]
    save = next(x for x in found if x[1] == "Save")
    assert save[4] == ["Translators: the button that saves"]
    text = format_entry(Message("A\nB {x}", refs=["m.py:3"]), ["C\nD {x}"])
    assert "#, python-brace-format" in text and 'msgid ""\n"A\\n"\n"B {x}"' in text
    assert i18n.parse_po(text)[0].msgstr == ["C\nD {x}"]


def test_pot_up_to_date():
    out = subprocess.run([sys.executable, str(ROOT / "tools/i18n_extract.py"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_german_left_scan():
    import i18n_check
    src = '''from x import _
log = None
def f():
    """Docstring über Dinge"""
    a = "Gerät verbunden"
    b = _("Device connected")
    log.info("läuft")
    c = f"Datei {a} gespeichert"
    d = "plain english"
    e = "Fehler"  # i18n: ignore
    g = dict(keywords=("hinzufügen",))
'''
    hits = [s for _l, s in i18n_check.german_left_in_source(src)]
    assert hits == ["Gerät verbunden", "Datei {} gespeichert"]


def test_frame_in_english():
    """the frame is English source text; the German text comes from locale/de/frame.po"""
    i18n.reload()
    i18n.set_language(None)
    # import while German is active: module constants (page titles) are translated once, at import
    from fm1companion.ui.pages import help as help_page
    try:
        i18n.set_language("en")
        assert i18n._("Overview") == "Overview"
        i18n.set_language("de")
        assert i18n._("Overview") == "Übersicht" and i18n._("&File") == "&Datei"
        assert i18n.ngettext("{n} problem", "{n} problems", 1).format(n=1) == "1 Problem"
        assert i18n.ngettext("{n} problem", "{n} problems", 3).format(n=3) == "3 Probleme"
        # help pages per language, German as fallback
        en = help_page.help_files("en")
        de = help_page.help_files("de")
        assert [f.name for f in en] == [f.name for f in de] and de and all(f.parent.name == "de" for f in de)
        es = help_page.help_files("es")
        assert [f.name for f in es] == [f.name for f in de] and all(f.parent.name == "es" for f in es)
    finally:
        i18n.set_language(None)


def test_cli_lang_option():
    env = dict(os.environ)
    env.pop("FM1C_LANG", None)
    out = subprocess.run([sys.executable, "-m", "fm1companion", "--lang", "de", "--help"], cwd=str(ROOT), env=env,
                         capture_output=True, text=True)
    assert out.stdout.startswith("Aufruf: fm1-companion") and "--lang" in out.stdout
    out = subprocess.run([sys.executable, "-m", "fm1companion", "--lang=en", "--help"], cwd=str(ROOT), env=env,
                         capture_output=True, text=True)
    assert out.stdout.startswith("Usage: fm1-companion")
    from fm1companion.app import take_lang_option
    argv = ["x", "--lang", "en", "--demo"]
    assert take_lang_option(argv) == "en" and argv == ["x", "--demo"]
    assert take_lang_option(["x"]) is None
