# Español (es)

Spanish translation of FM-1 Companion (neutral Spanish, informal "tú"). It is complete: every message of the app
and all help pages (`fm1companion/data/help/es/`) are translated. It was started with
`tools/i18n_new_language.py es` and is new – **corrections by native speakers are very welcome**.

Each `.po` file is one area of the app (the same split as `../de/`). Edit with any gettext editor (Poedit, Lokalize,
GNOME Translation Editor) or a text editor:

1. Keep placeholders such as `{name}` and `%s` exactly, keep HTML tags, choose your own accelerator letter after
   `&`. A plural entry needs a real singular and a real plural.
2. Use the names the app itself shows: the page is "Recuperación" (stages: "Etapa 1–3"), the button
   "Hacer copia ahora". When you rename a button or page, change every message and help page that quotes it.
3. Check and look at it:

   ```
   python3 tools/i18n_check.py --lang es
   FM1C_LANG=es python3 -m fm1companion --demo
   ```

The name in the language list of the app ("Español") is the header line `X-Language-Name` of these files.

The entries in `rescue.po` (used by `flash/texts.py`) are safety messages of the recovery tools – translate them
with the same meaning and strength, nothing softer, nothing stronger.

More: `docs/TRANSLATING.md`.
