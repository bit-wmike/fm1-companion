# SPDX-License-Identifier: GPL-3.0-only
"""Bug report: what is removed (SEC-09), where to send it (UX-04) and what the crash dialog may claim (ux §8)."""
import getpass
import importlib
import json
import platform
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home" / "michael"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for k, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k"), ("XDG_STATE_HOME", "s")):
        monkeypatch.setenv(k, str(home / sub))
    monkeypatch.delenv("FM1C_DEMO_ROOT", raising=False)
    monkeypatch.setattr(getpass, "getuser", lambda: "michael")
    monkeypatch.setattr(platform, "node", lambda: "studio-pi")
    from fm1companion import applog, config
    importlib.reload(config)
    importlib.reload(applog)
    yield home, config, applog
    importlib.reload(config)
    importlib.reload(applog)


def test_redact_text_patterns():
    from fm1companion import redact as R

    def r(t):
        return R.redact_text(t, home="/home/michael", host="studio-pi", user="michael")
    assert "hunter2" not in r("git clone https://michael:hunter2pass@github.com/x/y.git")
    assert r("git clone https://michael:hunter2pass@github.com/x/y.git").endswith("@github.com/x/y.git")
    assert "ghp_" not in r(f"remote: https://x-access-token:{TOKEN}@github.com/a/b")
    assert r("open https://private.example/sim?session=SESSIONSECRET123 now") == \
        "open https://private.example/sim?(entfernt) now"
    assert r("wlan0: associated with 3c:a6:2f:11:22:33 (SSID Bauer-WLAN-5G)") == "wlan0: associated with <mac> (SSID <netz>)"
    assert r('wpa: ssid="Mein Netz 5G" ok') == "wpa: ssid=<netz> ok" and r("bt 3C-A6-2F-11-22-33") == "bt <mac>"
    assert r("usb 1-1.2: SerialNumber: 3F2A9C0011223344") == "usb 1-1.2: SerialNumber: …3344"
    assert r("FM-1 serial 20240117000123 identity FM-1_91060") == "FM-1 serial …0123 identity FM-1_91060"
    assert r("/media/Michael/USB/kick.wav") == "/media/<benutzer>/USB/kick.wav"         # other capitalisation
    assert r("Michael Bauer's FM-1") == "<benutzer> Bauer's FM-1"
    assert r("Bluetooth: Michaels AirPods") == "Bluetooth: <benutzer>s AirPods" and r("michaelson") == "michaelson"
    assert r("/home/anna/Musik/x.wav") == "/home/<benutzer>/Musik/x.wav"                 # another user's folder
    assert r("user pi: /home/pi/FM-1 /run/media/pi/STICK/a") == "user pi: /home/<benutzer>/FM-1 /run/media/<benutzer>/STICK/a"
    assert r("/home/michael/FM-1/x on STUDIO-PI") == "~/FM-1/x on <rechner>"
    # nothing useful is destroyed
    for keep in ("serial port /dev/ttyACM0 opened", "time 12:34:56 ok", "version 6.11.0", "http://127.0.0.1:4000/sims/x/",
                 "USB 1d6b:0003 hub", "sha256 0f:1e"):
        assert r(keep) == keep, keep
    assert R.redact_text("a Stage-FM b /mnt/nas/FM-1/x", home="/h", host="", user="",
                         extra={"Stage-FM": "Device 1", "/mnt/nas/FM-1": "<path>"}) == "a Device 1 b <path>/x"
    assert R.mask_serial("ABCDEF123456") == "…3456" and R.mask_serial("123") == "123" and R.is_serial_key("usb_Serial")


def test_report_has_no_personal_details(env):
    home, config, applog = env
    pytest.importorskip("PyQt6.QtWidgets")
    s = config.load()
    s.user_root = "/mnt/nas-familie-bauer/FM-1"
    s.sample_roots = ["/srv/musik-bauer/Samples", str(home / "Samples")]
    config.module("catalog", {})["github_token"] = TOKEN
    config.module("simulator", {})["recent"] = [{"url": "https://private.example/sim?session=SESSIONSECRET123",
                                                 "title": "x", "kind": "emulator"}]
    config.save(s)
    dev = {"profiles": [
        {"id": "ab", "name": "Michaels FM-1 (Bühne)", "serial": "20240117000123", "notes": "geliehen von Anna, Tel 0170-1234567",
         "usb_path": "1-1.2", "usb_serial": "3F2A9C0011223344", "identity": "FM-1_91060"},
        {"id": "cd", "name": "Proberaum Keller", "serial": "20240117000999", "SerialNumber": 998877665544},
        {"id": "ef", "name": "FM-1", "serial": ""}]}
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "devices.json").write_text(json.dumps(dev), "utf-8")
    applog.log_dir().mkdir(parents=True, exist_ok=True)
    applog.log_file().write_text("\n".join([
        f"opened {home}/FM-1/Backups/Michaels FM-1 (Bühne)/a.bin",
        "connected: Michaels FM-1 (Bühne) and Proberaum Keller; FM-1 firmware Melodee",
        "/media/Michael/USB/kick.wav", "git clone https://michael:hunter2pass@github.com/x/y.git",
        "usb 1-1.2: SerialNumber: 3F2A9C0011223344", "FM-1 serial 20240117000123",
        "wlan0: associated with 3c:a6:2f:11:22:33 (SSID Bauer-WLAN-5G)",
        "scan /mnt/nas-familie-bauer/FM-1/Backups and /srv/musik-bauer/Samples/kick.wav",
        f"Authorization: Bearer {TOKEN}", "Linux studio-pi 6.6.31"]) + "\n", "utf-8")
    from fm1companion.ui import crash
    p = crash.build_report(home / "report.zip", "Traceback …\n  File \"/home/michael/x.py\"\nOSError: Michaels FM-1 (Bühne)")
    z = zipfile.ZipFile(p)
    blob = {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}
    body = "\n".join(t for n, t in blob.items() if n != "README.txt")
    for needle in ("michael", "Michael", "studio-pi", "ghp_", "SESSIONSECRET", "20240117000123", "3F2A9C0011223344",
                   "998877665544", "0170", "nas-familie-bauer", "musik-bauer", "hunter2", "3c:a6", "Bauer-WLAN",
                   "Bühne", "Proberaum", str(home)):
        hits = [n for n, t in blob.items() if needle in t]
        assert not hits, (needle, hits)
    devs = json.loads(blob["geraete.json"])["profiles"]
    assert [d["name"] for d in devs] == ["Device 1", "Device 2", "Device 3"]
    assert devs[0]["serial"] == "…0123" and devs[0]["usb_serial"] == "…3344" and devs[1]["SerialNumber"] == "…5544"
    # stable mapping: the same device has the same number in the logs; the generic name "FM-1" is left in running text
    assert "connected: Device 1 and Device 2; FM-1 firmware Melodee" in body
    assert "~/FM-1/Backups/Device 1/a.bin" in body and "OSError: Device 1" in blob["fehler.txt"]
    settings = json.loads(blob["einstellungen.json"])
    assert settings["user_root"] == "<path>" and settings["sample_roots"] == ["<path>", "~/Samples"]
    assert settings["modules"]["simulator"]["recent"][0]["url"] == "https://private.example/sim?(entfernt)"
    assert "scan <path>/Backups and <path>/kick.wav" in body
    # the README is honest about what is and is not removed, and says where to send the report
    readme = blob["README.txt"]
    assert crash.ISSUES_URL in readme and crash.ISSUES_URL == "https://github.com/bit-wmike/fm1-companion/issues"
    assert "may still contain file names" in readme and "before you post the report publicly" in readme
    assert "without personal paths" not in readme and "@" not in readme


def test_no_email_address_for_reports():
    src = (ROOT / "fm1companion" / "ui" / "crash.py").read_text("utf-8")
    assert "mailto:" not in src and "@" not in src.replace("@staticmethod", "").replace("@property", "")


def test_report_name_follows_the_language(env):
    pytest.importorskip("PyQt6.QtWidgets")
    from fm1companion import i18n
    from fm1companion.ui import crash
    old = i18n.current_language()
    names = {}
    for lang in ("en", "de", "es"):
        i18n.set_language(lang)
        names[lang] = crash.default_report_name()
    i18n.set_language(old or "de")
    assert names["en"].startswith("FM-1-Companion-bug-report-20") and names["en"].endswith(".zip")
    assert names["de"].startswith("FM-1-Companion-Fehlerbericht-20")
    assert names["es"].startswith("FM-1-Companion-informe-de-errores-20")
    assert all(" " not in n and "/" not in n for n in names.values())
    # "Storage and cleanup" still finds the reports of every language
    import fnmatch
    from fm1companion import storage
    for n in names.values():
        assert any(fnmatch.fnmatch(n, g) for g in storage.REPORT_GLOBS), n


def test_crash_dialog_does_not_promise_an_untouched_device(env):
    pytest.importorskip("PyQt6.QtWidgets")
    from fm1companion.ui import crash
    plain = 'Traceback (most recent call last):\n  File "/x/fm1companion/ui/pages/help.py", line 5, in show\nKeyError: 1'
    io = ('Traceback (most recent call last):\n  File "/x/fm1companion/flash/engine.py", line 5, in write_block\n'
          'OSError: [Errno 19] No such device')
    assert crash.device_write_possible(plain) is False
    assert crash.device_write_possible(io) is True
    assert crash.device_write_possible('  File "/x/fm1companion/transport.py", line 9, in send_raw\nOSError') is True
    # the device is held by an app operation (backup, restore, upload …)

    class Link:
        _exclusive_depth = 1

    class Ctx:
        link = Link()

    class Win:
        ctx = Ctx()
    assert crash.device_write_possible(plain, Win()) is True
    Link._exclusive_depth = 0
    assert crash.device_write_possible(plain, Win()) is False
    # a running firmware installation
    from fm1companion import storage
    orig = storage.install_running
    storage.install_running = lambda: True
    try:
        assert crash.device_write_possible(plain) is True
    finally:
        storage.install_running = orig
    # a message text that merely mentions the words is not a sign
    assert crash.device_write_possible("ValueError: backup restore transport.py /flash/") is False


def test_crash_dialog_texts(env):
    QtW = pytest.importorskip("PyQt6.QtWidgets")
    app = QtW.QApplication.instance() or QtW.QApplication([])
    from fm1companion import i18n
    from fm1companion.ui import crash
    i18n.set_language("en")
    try:
        d = crash.CrashDialog("Traceback\nKeyError: <b>1</b>", device_unknown=False)
        t = d.summary.text()
        assert "was not changed" not in t and "As far as the app can tell" in t
        assert crash.ISSUES_URL in t and "look through it before you post it publicly" in t
        assert "&lt;b&gt;1&lt;/b&gt;" in t
        d.add("Traceback\nOSError: x", device_unknown=True)          # a later error during a device job
        assert "not known whether the FM-1 was changed" in d.summary.text() and "Recovery" in d.summary.text()
        u = crash.CrashDialog("Traceback\nOSError: x", device_unknown=True)
        assert "not known whether the FM-1 was changed" in u.summary.text()
        assert "As far as the app can tell" not in u.summary.text()
        s = crash.ReportSavedDialog(Path("/somewhere/FM-1-Companion-bug-report-x.zip"))
        labels = " ".join(w.text() for w in s.findChildren(QtW.QLabel))
        assert crash.ISSUES_URL in labels and "look through it before you post it publicly" in labels
        assert s.b_issues.text() == "Open the issues page" and s.b_folder.text() == "Show file in folder"
        opened = []
        from PyQt6.QtGui import QDesktopServices
        orig = QDesktopServices.openUrl
        QDesktopServices.openUrl = staticmethod(lambda u: opened.append(u.toString()) or True)
        try:
            s.b_issues.click()
            s.b_folder.click()
        finally:
            QDesktopServices.openUrl = orig
        assert opened == [crash.ISSUES_URL, "file:///somewhere"]
    finally:
        i18n.set_language("de")
    del app
