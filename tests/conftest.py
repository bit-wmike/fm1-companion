# SPDX-License-Identifier: GPL-3.0-only
"""pytest setup.

Language: the tests check the German user interface, so the app language is German here (also inherited by
app processes the tests start). tests/test_i18n.py switches languages itself.

Home: no test may read or write the real home of whoever runs the suite. fm1companion.config works out its
folders (settings, data, cache and the default ``~/FM-1``) when it is imported, so HOME and the XDG folders are
pointed at a temporary folder right here, before any test module imports the app. A test that needs its own
home keeps setting one with monkeypatch; whatever it forgets lands in the temporary folder, never in the real
home. (Before this, a test that only set HOME after the import wrote into the real ``~/FM-1/Firmware``.)"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

os.environ["FM1C_LANG"] = "de"

REAL_HOME = Path(os.path.expanduser("~"))
TEST_HOME = Path(tempfile.mkdtemp(prefix="fm1c-tests-home-"))
os.environ["HOME"] = str(TEST_HOME)
for _var, _sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                   ("XDG_CACHE_HOME", ".cache"), ("XDG_STATE_HOME", ".local/state")):
    (TEST_HOME / _sub).mkdir(parents=True, exist_ok=True)
    os.environ[_var] = str(TEST_HOME / _sub)
atexit.register(shutil.rmtree, TEST_HOME, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _isolated_home():
    """every test runs with the temporary home; the app's own folders must lie inside it"""
    from fm1companion import config
    for p in (config.CONFIG_DIR, config.DATA_DIR, config.CACHE_DIR, Path(config.Settings().user_root)):
        assert TEST_HOME in Path(p).parents, f"{p} is outside the test home – the app was imported too early"
    yield
