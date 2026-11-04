# SPDX-License-Identifier: GPL-3.0-only
"""pytest setup: the tests check the German user interface, so the app language is German here
(also inherited by app processes the tests start). tests/test_i18n.py switches languages itself."""
import os

os.environ["FM1C_LANG"] = "de"
