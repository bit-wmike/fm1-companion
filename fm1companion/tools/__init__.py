# SPDX-License-Identifier: GPL-3.0-only
"""The tools the app bundles and drives: M-Flasher, m-bootloader and SLOOP 3 (pins in manifest.json).

manifest.py  the pinned versions and checksums     store.py    where each version lives, which one is used
runner.py    runs M-Flasher (read-only commands)    updates.py  release checks and the "Update" download
mflasher_parse.py  M-Flasher's text output
"""
