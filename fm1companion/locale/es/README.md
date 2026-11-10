# Español (es)

Translation of FM-1 Companion into Español, started with `tools/i18n_new_language.py es`.

Each `.po` file is one area of the app (the same split as `../de/`). Every `msgstr` is still empty – the app shows
the English text for those. Translate with any gettext editor (Poedit, Lokalize, GNOME Translation Editor) or a text
editor:

1. Check the header of every file: `Plural-Forms` must match Español
   (see https://www.gnu.org/software/gettext/manual/html_node/Plural-forms.html).
2. Translate the `msgstr` entries. Keep placeholders such as `{name}` and `%s` exactly, keep HTML tags,
   choose your own accelerator letter after `&`.
3. Check and look at it:

   ```
   python3 tools/i18n_check.py --lang es
   FM1C_LANG=es python3 -m fm1companion --demo
   ```

4. Optional: add the name of the language in its own language to `LANGUAGE_NAMES` in `fm1companion/i18n.py`
   (otherwise the language list shows the code `es`), and translate the help pages
   (`fm1companion/data/help/en/*.md` → `fm1companion/data/help/es/`).

The entries in `rescue.po` (used by `flash/texts.py`) are safety messages of the recovery tools – translate them
with the same meaning and strength, nothing softer, nothing stronger.

More: `docs/TRANSLATING.md`.
