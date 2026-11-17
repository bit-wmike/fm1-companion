# How a release is made

A release is made by GitHub, not on anyone's computer. You need a browser or the GitHub app – no git, no terminal.
Nothing becomes public until you press **Publish release** at the very end.

## The three steps

1. **Merge the pull request** into `main`.
   Wait until the checks on `main` are green (repository page › the small tick next to the last commit, or
   **Actions** › *Tests*).

2. **Start the release:** **Actions** › **Release** (list on the left) › **Run workflow** › branch `main` ›
   green button **Run workflow**.
   It runs for about 30 to 60 minutes. You can close the page. It does not publish anything.
   *(In the GitHub app the button may be missing – then open github.com in the phone's browser, or ask whoever
   maintains the code to start it.)*

3. **Publish:** when the run is green, open **Releases**. At the top is a release marked **Draft**, named
   "FM-1 Companion Suite <version>". Open it and check:
   * the text reads well and the version is right,
   * under **Assets** there are the `.deb`, the `.run`, `SHA256SUMS` and about 35 files ending in `.tar.gz`
     (the source code of the bundled firmwares – they must be there).

   Then press **Publish release** (in the app: *Edit* › *Publish*). Leave "Set as the latest release" switched on.
   GitHub creates the tag `v<version>` at that moment. Done.

After publishing, a short second run called *Release* appears under Actions and says "already published – nothing to
do". That is expected.

## What the workflow does for you

* checks that the version number is the same in the app, the README, the man page and the change list;
* runs all tests on Debian Bookworm and Debian Trixie (the two Raspberry Pi OS versions);
* builds the `.deb` and the `.run` from the clean repository – twice, and compares the results;
* fetches the exact source code of every bundled firmware build (more than 30 commits from 16 projects) and
  attaches it – the GPL requires this, and every twin's NOTICE file promises it;
* writes `SHA256SUMS`;
* writes the release text from the entry of this version in `packaging/fm1-companion.metainfo.xml`, adds the
  install commands and the honest note about what was tested on a real FM-1 (taken from the same table the app
  shows, `fm1companion/flash/tested.py`);
* creates the draft. Starting the workflow again replaces the draft; a published release is never changed.

## If something is red

Open the red run under **Actions** and look at which box is red.

| Red box | What it means | What to do |
|---|---|---|
| **Which version?** – "already published" | This version is out already. | A new release needs a new version number – see "Before the next release". |
| **Which version?** – "… does not say 2.x.y" | The version number differs between files. | Fix the named file in a pull request (see below), merge, start again. |
| **Tests / Lint and metadata** | A quick check failed (translation incomplete, version mismatch, syntax). | The message names the file. Fix in a pull request, merge, start again. |
| **Tests / Debian bookworm** or **Debian trixie** | A test failed on that system, or the `.deb` could not be installed there. | Do not publish. Open the step that is red, copy the last lines and hand them to whoever maintains the code. If the red step is *System packages*, it is usually a network hiccup at Debian: press **Re-run failed jobs**. |
| A yellow note "UI scripts failed" while the job is green | The UI tests failed; this is shown but does not block yet. | Look at the table in the run's summary. A release is still possible; tell the maintainer. |
| **Build and draft the release** – "is not reproducible" | Two builds of the same commit differ. | Do not publish; report it. |
| **Build and draft the release** – "could not fetch … at commit …" | A firmware project or one commit of it is no longer on GitHub. | First press **Re-run failed jobs** (it may be a network problem). If it stays red: **do not publish** – without that source code the release would break the GPL. The twin in question has to be rebuilt from an available commit or removed. |
| A yellow note "The metainfo dates … on …, today is …" | The date in the change list is not today's. | Harmless. If you care: correct the date in `packaging/fm1-companion.metainfo.xml`, merge, start again. |

Pressing **Re-run failed jobs** (top right of a run) is always safe. So is starting the workflow again.
A draft you do not want: open it under Releases and press **Delete** (the bin icon) – nothing public is affected.

## Before the next release (for whoever changes the code)

In one pull request:

1. the new version in `fm1companion/__init__.py`, the README title, the first line of `packaging/fm1-companion.1`
   and the placeholders in `.github/ISSUE_TEMPLATE/bug_report.yml` and `hardware_test.yml`
   (the check "The version is the same everywhere" fails until all agree);
2. a new `<release version="…" date="YYYY-MM-DD">` entry at the top of the list in
   `packaging/fm1-companion.metainfo.xml` – this is the changelog and becomes the release text. Write it for
   musicians, and say what was not tested;
3. `docs/UNTESTED.md` and `fm1companion/flash/tested.py` only change after a hardware test report;
4. the tests that state the version (`tests/test_appstart.py`, `tests/test_packaging.py`, `tests/ui_shell_test.py`).

Tests that need the original M-VAVE firmware files or local firmware checkouts are skipped on GitHub (those files
cannot be in the repository). Whoever has them runs the full suite locally before a release:
`FM1_TEST_PKGDIR=… python3 -m pytest tests/test_*.py fm1companion/vendor/unbricker/tests -rs`.

## Never

* Never move or delete a tag `v…` of a published release, and never replace its files: the checksums and the
  promise "exactly this commit" depend on them. A mistake in a release is fixed by the next version.
* Never build release packages on a working machine.
