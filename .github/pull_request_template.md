## What and why

<!-- Briefly: what changes, which problem does it solve? Link an issue, if there is one. -->

## Tests

<!-- Which tests did you run (commands, result)? New tests for new features. -->

- [ ] `QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_*.py -q`
- [ ] affected UI scripts (`QT_QPA_PLATFORM=offscreen python3 tests/ui_<area>_test.py`)

## Checklist

- [ ] Writes nothing to the FM-1 without being asked; new write paths have a confirmation and are listed in
      docs/UNTESTED.md as long as they have not been tested on real hardware
- [ ] No personal data, tokens, home paths or host names in code, tests or docs
- [ ] UI texts go through the translation system; SPDX header in new Python files
- [ ] PR description, commit messages and docs are in English
- [ ] I agree that my contribution is published under GPL-3.0-only
