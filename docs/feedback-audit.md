# Feedback per action – checklist (area pages/sound/music/samples/data pages)

Rule: every action says **what happened**, also for partial results, and, where it matters, **what did not
happen**. No silent success, no bare "OK".

The app's messages are currently German; the quoted messages below are English renderings of them (the UI
translation is in progress, see [TRANSLATING.md](TRANSLATING.md)).

Means: `JobDialog(..., done_text=, fail_text=, actions=, cancellable=)` shows "<title> – running …" → green
"Done" + result line or red "Failed" + explanation, log under "Details", button
"Close". `run_job(..., done_text=, fail_text=, notify_fail=)` shows toasts for background jobs.
Without `done_text` the dialog takes the last log line, never an empty "OK".

| Page / action | before | after |
|---|---|---|
| **All JobDialogs** | heading "… – done", button "Close"/"Schließen", nothing else | "running …" / ✓ "Done" + result line / ✗ "Failed" + explanation, collapsible details, "Cancel" (or greyed out with a reason) |
| File errors in jobs | "[Errno 13] Permission denied: …" | "No write permission (…) – please check the permissions …" |
| Compose · Write to pattern | status line | dialog: "Track k, pattern b: n steps written and read back. Old content backed up. Permanent only after SAVE." |
| Compose · Undo | status line | dialog: "… has its old content again."; on a different device: refusal naming the device |
| Compose · Save as MIDI | status line, crash on file error | status line "only as a file, nothing changed on the FM-1" + toast; file error in plain language |
| Workshop · Play onto the FM-1 | log "Done"; without a file: nothing | "n patterns written (…); song chain replaced/unchanged. Old state backed up."; abort: "x patterns read beforehand – Undo brings them back" or "nothing changed"; without a file: explanation |
| Workshop · Undo | log only | "n patterns (and the song chain) have their old state again." |
| Presets · Read/Back up/Write/Delete/Rename/Move/Save track | status line | status line with numbers; on partial failure: "Slots already changed can be reverted with Undo." |
| Presets · Tags / Remove | silent | status line "… (library only, nothing changed on the FM-1)" |
| Presets · Export | status line | status line + toast, "nothing changed on the FM-1" |
| FM voices · Load collection | "done (…)" | "downloaded … – nothing was sent to the FM-1" + toast |
| FM voices · To your own bank | silent | status line with count |
| FM voices · FM6 preset / Undo / SysEx | status line | unchanged (already clear), undo per device |
| Samples · Load kit into slot | dialog "done" | "Kit "X" (n sounds) is now in USRk. Old content backed up – Undo"; error: whether backed up or "nothing changed" |
| Samples · Clear slot / Undo | dialog "done" / – | "USRk is empty. Old content backed up" / "USRk has its old content again" |
| Samples · Export as WAVs | success shown as a **warning** dialog (show_error) | toast "n WAV files exported to …" |
| Samples · Save kit | silent | toast "Kit "X" saved (n sounds) – on the computer only" |
| Samples · Trim to fit | silent | toast "only in the kit builder, files unchanged" |
| Samples · Drag files here | silent (only re-scan) | toast with count |
| Samples · FM1 Open read / write | dialog "done" | "read and backed up: file – nothing changed on the FM-1" / "written and verified byte by byte; previous state as .fm1s" |
| Samples · MP3 without ffmpeg | length "?" | hint "for MP3/FLAC: sudo apt install ffmpeg" (list and tooltip) |
| Sample packs · Load | status line | status line + toast (success/error) |
| Recording · Export | device line | + toast "n of m exported (next to the original), k skipped" |
| Recording · As sample / Delete | line / silent | + toast, file errors in plain language |
| Projects · Fetch | dialog "done" | "n of m project(s) fetched into the library – nothing changed on the FM-1" |
| Projects · Clear slot / Load in | dialog "done" | "Slot k is now empty" / ""X" is now in slot k – load it via SAVE > PROJECT"; error: "the device did not write anything" |
| Project view · Read from FM-1 | dialog "done" | "… read and opened – only viewed, nothing changed on the FM-1" |
| Project view · Exports | toast | toast; file errors in plain language instead of a crash |
| Backup browser · Safety backup / Restore | dialog "done" | "Current state backed up: file. Nothing restored yet" / "n parts restored; previous state in the safety backup …" |
| Backup browser · Read live state | dialog "done" | closes by itself; "only in memory, nothing changed on the FM-1" |
| Firmware+ · Update catalog | silent | toast with number of entries; error: hint "saved state" |
| Firmware+ · Save link | silent | toast "saved – nothing was installed"; file errors in plain language |
| Sound module · Start / Stop | toast / silent | toast / toast |
| MIDI · Export log | toast; crash on file error | toast; file errors in plain language |
| Devices · Compare | A = B possible | button greyed out with a reason; A ≠ B preselected |

Not in this area (checked separately): firmware page (download/installation), backups page, overview,
rescue, main window – they use the same JobDialog API.

## Feedback – installer, firmware, backups, rescue, device (ws/fixa)

Every action reports at the end what happened and what did not (Done/Failed via `JobDialog(done_text=…)`,
wrapper `ui/feedback.run`, small actions via the status line `ctx.message`).

| Action | Where | Feedback on success | on error / abort |
|---|---|---|---|
| Install firmware | Firmware, catalog, own file | "Installation finished – the FM-1 now reports <identity> (<firmware version>), via the original V15, resources written / NOT written, backup saved beforehand / without backup." | reason in plain language; before V15: "nothing was changed on the FM-1"; after V15: "The original firmware V15 was written to the FM-1 … <target> was NOT written" |
| Dry run | plan "Nur Probelauf" (dry run only) | "Dry run successful – all packages checked, the FM-1 answered. Nothing was changed on the FM-1." | reason (e.g. package damaged), nothing sent |
| Download only | Firmware | "<firmware> <version> downloaded (<file>, checksum verified / not available) – NOT installed yet. Saved in …" + [Install now …] [Open folder] | HTTP 403/404/offline in plain language, details under "Show details" |
| Rewrite font & factory sounds | Firmware | "Resources of Melodee <tag> written … The firmware itself was not changed." | reason in plain language |
| Add / remove firmware | Firmware | status line: "… added – nothing installed yet" / "… removed. Packages remain, nothing changes on the FM-1." | – |
| Release list | Firmware | "loading …", then the list | "No versions found. <reason: offline / rate limit / no releases>" |
| Backup now | Backups, overview, Ctrl+B | "Backup saved: <file> (57 parts, 12 projects, 412 KB). Nothing was changed on the FM-1." + [Open folder] | firmware without backup: "<firmware> has no editor protocol – the app cannot make a backup of it …"; read-only: "No write permission …" |
| Restore everything | Backups | "Restore finished: N parts from the state of <date> written … The previous state is in the safety backup <file>." (or: "There is NO safety backup") | different firmware family: "Restore not possible" + what is possible |
| Mirror | Backups | "Mirror updated: N backups copied and verified, no errors. Target: …" | number of errors |
| Note / labels / pin / add / delete | Backups | status line: "<file>: note saved", "pinned – will never be deleted automatically", "Backup added (copy)", "Deleted: …" | "Not saved … (read-only folder?)" |
| Extract projects | Backups | info: "N projects are now in the project library … Nothing was changed on the FM-1." | – |
| Damaged backups | Backups | shown red as "damaged" in the list, count in the header | open/restore: "cannot be read … deleting works" |
| Rename | Overview | "Renamed to "X" – backups are now in "X"." | "Renaming not possible" + reason |
| Choose firmware (ambiguous identity) | connecting a device | "Remembered: <device> runs <firmware> (identity …)" / "Firmware unknown – installations count as a switch" | – |
| Reconnect | main window | "Reconnecting to …" / "N FM-1 found" / "no FM-1 found – check the USB data cable" | – |
| FM-1 page "Neu einlesen" (re-read) | FM-1 | "read at HH:MM:SS (read only, nothing changed)"; generic: what is known + why there are no live values | "Error: …" |
| Rescue: finish installation | Rescue | "Installation finished – the FM-1 now reports <identity> and starts normally." | engine message (in plain language) |
| Rescue: fetch tool / V15 in ROM mode / repair settings / full backup | Rescue | in each case, what was written or NOT written (simulation: "nothing was written") + [Open folder] | explanation of the code |
| Settings broken | start | one-time notice: file moved aside / invalid fields replaced | – |
