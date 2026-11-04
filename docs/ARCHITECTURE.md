# FM-1 Companion Suite – Architecture notes

How the code is organised and which rules hold it together. For setting up a development environment,
running the tests and the safety rules for contributions see [CONTRIBUTING.md](../CONTRIBUTING.md).

## 1. Code layout

```
fm1companion/
  __init__.py        version, APP_NAME, APP_ID
  app.py             entry point (--demo, --check, --version)
  config.py          Settings dataclass; config.module(name, defaults) per-area settings; config.folder(name)
  protocol.py        Felucca SysEx framing (F0 7D 46 4C cmd ... F7), u32/r32 (5x7 bit), v14/r14, pack7, crc32,
                     Reader, Cmd enum, parse_info, parse_audio_stats (+schemas), LIBRARY list parsing
  transport.py       MidoTransport, SubprocessTransport (hex lines), Link (request/response, exclusive/hold,
                     passthroughs for web pages, listeners for monitoring, send_midi)
  devices.py         port scan, USB details via sysfs, device profiles (ProfileStore)
  backup.py          backup format of the Melodee web editor (fm1backup.js port): capture/restore/preflight
  projects.py        single project slots (.fm1proj), library
  firmware.py        FirmwareSpec (+ catalog fields), releases (GitHub), download+SHA, tools clone, install()
  editor_host.py     prepares the firmware's own web editor (git archive) + LocalServer (127.0.0.1)
  bridge.py          MidiBridge (QObject) – Web-MIDI <-> Link for QtWebEngine pages
  web/webmidi_polyfill.js   navigator.requestMIDIAccess over QWebChannel
  health.py          PiMonitor (Pi values), fm1_report/fm1_live/fm1_presets (FM-1 values)
  recorder.py, samples.py, midi_import.py, demo_device.py (FakeFM1 simulation), vendor/sampleio.py
  ui/
    main_window.py   window, grouped navigation (built-in pages + auto-discovered pages), COMPOSITES/ALIASES
    tabbed.py        TabbedPage (several pages as tabs of ONE page) and TabAdopter (extra tabs in an existing
                     QTabWidget of a page) – how the navigation is kept at 13 entries
    pages/__init__.py  PageSpec + discover()  <-- NEW PAGES GO HERE (one file per page/area)
    web.py           shared QWebEngineProfile (only ONE per process!), RoutingPage, make_view()
    device.py        DeviceContext (ctx): current port, link, info, profile; scan; job()
    common.py        icon(), fmt_size/fmt_secs/fmt_date, run_job(), JobDialog, Card, heading(), confirm(danger=),
                     show_error(), empty_state(), Disclosure (collapsible „Details“), set_variant(), fw_label()
    theme.py, overview.py, fm1_page.py (tab „Zustand“ of Übersicht), firmware_page.py, editor_page.py,
    projects_page.py, backups_page.py, samples_page.py, audio.py (Player, Waveform), recorder_page.py,
    workshop_page.py (tab „Aus MIDI-Datei“ of Komponieren), settings_dialog.py
tests/               pytest unit tests + offscreen UI tests (see section 6)
docs/                design notes and research per area (docs/<area>.md)
```

## 2. Architecture rules

### Navigation (13 entries) and merged pages
Since the usability pass the sidebar has 13 entries in 5 groups (Firmware and Entdecken were merged into **Firmware+** in 2.1; Firmware+ and Rettung live under Daten & Werkzeuge). Group, entry and tab names below are the German UI labels (the UI is not translated yet):

| Group | Entry | Tabs (old page key → tab) |
|---|---|---|
| Gerät | Übersicht | Start (`overview`), Zustand (`fm1`) |
| | Editor, Geräte | – |
| Klang | Presets | User-Presets am FM-1 (`presets`), FM-Voices (DX7) (`voices`) |
| | Samples & Kits | Bibliothek & Kits, Auf dem FM-1 (sample slots + FM1 Open), Aufnehmen, Sample-Pakete |
| Musik | Projekte | Plätze & Bibliothek (`projects`), Ansicht & Export (`project_view`) |
| | Komponieren | Erzeugen (`compose`), Aus MIDI-Datei (`workshop`) |
| | Aufnahme | – |
| Daten & Werkzeuge | Backups | Liste (`backups`), Durchsuchen & Vergleichen (`backup_browser`) |
| | Firmware+ (`discover`) | Alle Firmwares, Installieren & Versionen (`firmware`), Neuigkeiten, Werkzeuge & Wissen, Simulator (`simulator`) |
| | Rettung (`recovery`) | – |
| | MIDI | Monitor, Tastatur, Takt, Regler, Verbindungen, Klangerzeuger (`soundmodule`) |
| Hilfe | Hilfe | Hilfe, Diagnose |

The merge is declared in `ui/main_window.py` (`COMPOSITES` → `TabbedPage`, `FLAT_COMPOSITES` → `TabAdopter`,
`ALIASES` derived from both). **Old page keys keep working**: `window.go("backup_browser", path=...)` opens
Backups › Durchsuchen & Vergleichen and passes the kwargs to that sub-page; `window.page("project_view")` returns
the sub-page; `window.page("backups")` returns the TabbedPage, which forwards unknown attributes to its main
sub-page (so `window.page("backups").tree` still works). `window.go(key, tab="…")` selects a tab explicitly;
`window.go("overview")` always lands on the Start tab. Sub-pages keep their own `activate/deactivate/shutdown`
hooks – they are called when their tab is shown/left. The command palette lists the tabs of merged pages as
separate entries. `HOTKEY_ORDER` fixes Ctrl+1…9.

Firmware+ (`discover`) hosts the former Firmware page (`ui/firmware_page.py`, `window.firmware`) as the tab
„Installieren & Versionen“ (`FLAT_TAB_POS` puts it right after „Alle Firmwares“). The old key `firmware` is an alias:
`window.page("firmware")` is the `FirmwarePage`, `window.go("firmware", install=spec)` switches to that tab and runs
its `open_with`. The flashing code paths themselves are unchanged. If the catalog page cannot be loaded, the
FirmwarePage takes its place in the sidebar, so installing and „Zurück zum Original“ always stay reachable.

### Pages (self-registering)
Create `fm1companion/ui/pages/<your_area>.py` and define

```python
from . import PageSpec
def _make(ctx, window):
    return MyPage(ctx, window)
PAGE = PageSpec(key="simulator", title="Simulator", group="klang", factory=_make,
                icons=("media-playback-start",), order=40,
                description="FM-1-Simulatoren im Rechner spielen", keywords=("emulator", "wasm"))
```
(or `PAGES = [...]` for several). Groups: `geraet, klang, musik, daten, entdecken, werkzeuge, hilfe`.
Page hooks (all optional): `activate()`, `deactivate()`, `open_with(**kwargs)` (deep link from other
pages: `window.go("simulator", url=..., title=...)`), `shutdown()` (stop processes, timers),
signal `navigate = pyqtSignal(str)`. A page that crashes on import is skipped and reported — but don't.
Cross-page jumps: `window.go(key, **kwargs)`; check `window.page(key)` exists, else degrade gracefully
(e.g. open the URL externally).

### Device access
`ctx` (DeviceContext, `fm1companion/ui/device.py`):
`ctx.port` (PortInfo), `ctx.link` (Link), `ctx.info` (Info from Felucca INFO), `ctx.profile`,
`ctx.connected` (= Felucca protocol usable), `ctx.firmware_key`, `ctx.firmware_tag`, `ctx.settings`,
`ctx.store` (profiles), signals `changed`, `ports_changed`, `message(str)`, `editor_pause(bool)`.
* `ctx.require(parent)` → shows a hint and returns False if no device.
* `ctx.job(parent, title, fn, dialog=True|False, on_done=...)` runs `fn(worker, link, ...)` in a background
  thread with **exclusive** access (web editor pauses). Use it for every request/response exchange.
* Never call `link.request()` in the GUI thread. Never block the GUI thread (no sleep, no network).
* Monitoring: `link.add_listener(fn)` → `fn(bytes, "in"|"out")` for EVERY message, called from the MIDI
  thread → hand over to the GUI thread via a Qt signal. Remove it in `deactivate/shutdown`.
* Plain MIDI from the app (notes, CC, clock): `link.send_midi(bytes)` (refused during exclusive ops).

### Background work and UI helpers (`ui/common.py`)
`run_job(parent, fn, *args, on_done=, on_fail=(msg, tb), on_log=, on_progress=)` → `fn(worker, *args)`
in a daemon thread (worker.log.emit(str), worker.progress.emit(done, total)); `JobDialog` for modal
progress; `show_error(parent, title, text)`, `confirm(parent, title, text, ok_text, danger=False)`,
`heading(title, subtitle)` at the top of every page, `Card(title)`, `icon(*names)`, `fmt_size`, `fmt_secs`.

### Responsive layout (`ui/responsive.py`)
The app must stay usable on half a 1080p screen (content ≈ 940 px wide). Pages must not force the window wide:
* The page stack sits in a scroll area (`window.page_scroll`/`PageHost`); only the **visible** page/tab counts for
  the minimum size (`current_only()` is applied to every QStackedWidget/QTabWidget of the window).
* Two-column layouts: `adaptive_splitter(splitter[, breakpoint])` – side by side when wide, stacked when narrow
  (automatic breakpoint = sum of the columns' minimum widths; hysteresis, no flicker).
* Button rows: build them with `flow_row(...)`/`FlowLayout`, or call `reflow(hbox)` after `addLayout(hbox)`.
* Anything else: `on_narrow(widget, px, callback)`, `form_wrap(form, host, px)`, `fit_height(widget)` (minimum
  height follows wrapped rows inside a vertical QSplitter).
* Sidebar: automatic icon mode below 1150 px window width; `window.set_sidebar_mode("auto"|"wide"|"icons")`.
* `tests/ui_responsive_test.py` checks every page and tab at 950×1000 – run it when you add a page.

### Web pages (QtWebEngine)
Only ONE QWebEngineProfile may exist: always use `ui/web.py`:
`web.make_view(parent, bridge=MidiBridge|None, router=fn|None, allow_remote=False)` → (view, page, channel).
`allow_remote=True` lets the view navigate to internet sites (online editors/emulators); otherwise
external links open in the system browser. `web.add_download_handler(fn)` (fn(item) -> True if handled).
The polyfill gives every page `navigator.requestMIDIAccess` through the MidiBridge.
`editor_host.LocalServer` serves `DATA_DIR/editor/...` and the bundled simulators on 127.0.0.1 (only requests
with `Host: 127.0.0.1:<port>`/`localhost:<port>` are answered, everything else gets 421). Pages that may load
internet content never get editor SysEx to the real FM-1 without the per-origin consent in `sims.OutGate`.

### Settings and files
* Per-area settings: `d = config.module("simulator", {"firefox": True}); d["x"] = 1; config.save()`.
  Add fields to the Settings dataclass only for app-wide settings (e.g. `risk_ack`). `config.json` is written
  with mode 0600 because it may contain a GitHub token.
* User-visible files: `config.folder("DX7")` → `~/FM-1/DX7` (created). Existing: Backups, Projekte,
  Samples, Kits, Aufnahmen, Firmware.
* Internal data: `config.DATA_DIR / "<area>"`; caches: `config.CACHE_DIR / "<area>"`.

### Dependencies
Allowed at runtime: stdlib, PyQt6 (+WebEngine, WebChannel, optional QtMultimedia), numpy, mido+rtmidi,
plus command-line tools from Debian packages (ffmpeg, alsa-utils: aconnect/arecord/aplay, pipewire-bin:
pw-record/pw-play, fluidsynth, git, vcgencmd, journalctl, dmesg …). Anything optional must be imported
lazily with a clear user-facing hint ("sudo apt install …") when missing. Every new apt package goes into
`fm1companion/data/deps.json` (the single list used by the installers, the .deb and `--check`).
Python **3.11** syntax only (no PEP 695 `type`/generics, no 3.12 f-string quote nesting); PyQt6 **6.4**
APIs only; numpy **1.24** APIs only (no numpy 2-only functions); mido 1.2.10.

### UI quality bar ("gekaufte Software" – feels like bought software)
UI texts are currently German and go through the translation system (English is in progress, see
[TRANSLATING.md](TRANSLATING.md)); consistent wording ("FM-1", "Firmware", "Backup", "Preset", "Projekt", "Sample").
Every page: `heading()`, sensible empty states ("Noch keine … – so geht's: …" – none yet, here is how), tooltips,
no "TODO", no untranslatable hard-coded UI strings, human-readable errors, progress for long operations, cancel where safe,
keyboard shortcuts where natural, works at 1280×800 (use scroll areas), dark and light theme.
**Device safety:** never write to the FM-1 without an explicit user action and a confirmation that says
exactly what will be overwritten; read-before-write so the user can undo; prefer backups first.

### Licensing
The app is **GPL-3.0-only**. Every Python file starts with `# SPDX-License-Identifier: GPL-3.0-only`.
Vendored/derived code must be GPL-3.0-compatible; keep its SPDX header and original copyright, and list it in
`fm1companion/data/credits.json` (shown under Hilfe › Über › Mitwirkende & Lizenzen).
