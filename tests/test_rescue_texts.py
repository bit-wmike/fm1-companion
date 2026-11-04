# SPDX-License-Identifier: GPL-3.0-only
"""flash/texts.py: the German messages of the pinned rescue files (flashmodel.py, restzone.py, rescuegate.py) in the
UI language.

(a) coverage: every German text literal of the three files is matched by a rule (explicit allowlist for strings the
    user never sees);
(b) round trip: with German as the UI language the shown text is exactly the original – for the templates in
    locale/de/rescue.po and for real output of the three files;
(c) English: real output comes out English, numbers, addresses and checksums intact; unknown text unchanged.
"""
from __future__ import annotations

import ast
import contextlib
import json
import logging
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from fm1companion import i18n  # noqa: E402
from fm1companion.flash import flashmodel as fm, rescuegate as rg, restzone as rz, texts  # noqa: E402
import i18n_check  # noqa: E402

PINNED = [ROOT / "fm1companion" / "flash" / n for n in ("flashmodel.py", "restzone.py", "rescuegate.py")]

# German literals of the pinned files the user never sees (with the reason)
ALLOWLIST = {
    # argparse help of the command line (the app passes arguments itself, nobody reads --help)
    "Unbricker restore / repair-settings mit Prüfung der frischen Sicherung",
    "known_packages.json (Standard: neben dem Skript bzw. data/)",
    "Demo-Firmwares und Demo-Kopf dazunehmen (nur --dry-run)",
    "V16-Reste 0x93000–0xA8FFF löschen (ROM-Bootmodus)",
    "Ordner für Sicherungen und Journal",
    "clean: nur diese Sektoren (0x…,0x…)",
    "V16-Paket für Teilsektoren (optional)",
    "Demo-Firmwares dazunehmen (nur mit --dry-run)",
    "Mock-FM-1 des Unbrickers statt echtem Gerät",
    "Mock: Absturz nach N Löschbefehlen simulieren",
    # suffix of the journal's temporary file name
    ".neu",
}


def _norm_literal(node) -> str:
    """a string literal with its variable parts (f-string fields, %s/%d) as \\0"""
    if isinstance(node, ast.JoinedStr):
        s = "".join(v.value if isinstance(v, ast.Constant) else "\0" for v in node.values)
    else:
        s = node.value
    return re.sub(r"%[sd]", "\0", s)


def _german_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text("utf-8"))
    skip = i18n_check._docstring_nodes(tree)
    inner = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    out = []
    for n in ast.walk(tree):
        if id(n) in skip or id(n) in inner:
            continue
        if isinstance(n, ast.JoinedStr) or (isinstance(n, ast.Constant) and isinstance(n.value, str)):
            s = _norm_literal(n)
            if i18n_check.looks_german(s.replace("\0", " ")):
                out.append((n.lineno, s))
    return out


def _covered(fragment: str) -> bool:
    pieces = [p.strip() for p in fragment.split("\0") if p.strip()]
    rx = re.compile(".*?".join(re.escape(p) for p in pieces), re.S)
    templates = [r.de for r in texts.RULES] + [k for r in texts.RULES for kind in r.kind.values()
                                               if isinstance(kind, dict) for k in kind]
    return any(rx.search(re.sub(r"\{\w+\}", "\0", t)) for t in templates)


def test_a_every_german_text_of_the_pinned_files_is_covered():
    missing = []
    seen = 0
    for path in PINNED:
        for line, s in _german_literals(path):
            seen += 1
            if s in ALLOWLIST:
                continue
            if not _covered(s):
                missing.append(f"{path.name}:{line}: {s!r}")
    assert seen > 150
    assert not missing, "German texts without a rule in flash/texts.py:\n" + "\n".join(missing)


def test_a_allowlist_has_no_stale_entries():
    found = {s for p in PINNED for _l, s in _german_literals(p)}
    assert ALLOWLIST <= found


def test_a_pinned_files_unchanged_and_texts_not_imported_by_them():
    from fm1companion.flash import uboot, uboot_manifest
    import hashlib
    for name, path in uboot.OWN_FILES.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == uboot_manifest.OWN[name], name
    for p in PINNED:
        assert "texts" not in p.read_text("utf-8").split('"""', 2)[-1].split("def ", 1)[0]
    assert "texts.py" not in uboot_manifest.OWN


# ====================================================================== language switching
@contextlib.contextmanager
def lang(code):
    old = i18n.current_language()
    i18n.set_language(code)
    try:
        yield
    finally:
        i18n.set_language(old)


def test_b_catalog_reproduces_the_german_templates_exactly():
    cat = i18n.catalog("de")
    assert not cat.errors
    for r in texts.RULES:
        en = r.en[0] if isinstance(r.en, tuple) else r.en
        got = cat.messages.get((None, en))
        assert got, f"not in the German catalog: {en!r}"
        assert all(g == r.de for g in got), f"{en!r}: {got!r} != {r.de!r}"
        for k in r.kind.values():
            if isinstance(k, dict):
                for de, en2 in k.items():
                    assert cat.get(None, en2) == de
    assert sorted({r.de for r in texts.RULES}) == sorted(r.de for r in texts.RULES), "duplicate German template"


# ====================================================================== real output of the pinned files
class _Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _ref_like():
    return SimpleNamespace(identity="FM-1_015", file_sha256="x", write_end=fm.V15_END, head_sha256="a", layout={})


def produce(tmp_path) -> list[str]:
    """texts the three files really produce (reports, reasons, log lines) – without real packages"""
    out: list[str] = []
    refs = fm.load_known() + fm.demo_refs()
    heads = {fm.FACTORY_HEAD_SHA256} | fm.demo_heads()

    def rep(r):
        out.extend([r.title, r.firmware, *[t for _lv, t in r.findings], *r.details, *r.not_covered, *r.compared])

    dumps = fm.demo_dumps()
    for d in dumps.values():
        rep(fm.check_dump(d, refs, factory_heads=heads))
        rep(fm.check_dump(d, refs))                                   # demo head unknown
    rep(fm.check_dump(b"\xff" * fm.FLASH_SIZE, refs))
    rep(fm.check_dump(bytes(fm.FLASH_SIZE // 2) + b"\x12" * (fm.FLASH_SIZE // 2), refs))
    with pytest.raises(fm.DumpError) as e:
        fm.check_dump(b"\0" * 1000, refs)
    out.append(str(e.value))
    for ident, ver, st, err in (("", "", None, ""), ("FM-1_015", "", None, ""), ("FM-1_91060", "Melodee 1.6.0-rc.7",
                                {"installed": True, "length": 1, "crc": 2}, ""),
                                ("FM-1_99999", "x", {"installed": False, "length": 0, "crc": 0}, ""),
                                ("FM-1_015", "", None, "no answer to command 82")):
        rep(fm.quick_check(ident, ver, st, refs, res_error=err))
    for cur, key, tgt in (("FM-1_91060", "melodee", "FM-1_016"), ("FM-1_015", "", "FM-1_016"),
                          ("FM-1_016", "", "FM-1_015")):
        out += fm.describe_loss(cur, key, tgt)
    cur, tgt = _ref_like(), _ref_like()
    tgt.identity, tgt.write_end, tgt.layout, tgt.head_sha256 = "FM-1_016", 0xA9000, {"SEQ64": (1, 2)}, "b"
    out += [t for _lv, _c, t in fm.preflight(tgt, cur, "FM-1_v15.fwsc", set(), mode="from4000")]
    out += fm.app_directory(b"\0" * 0x100000)["errors"] + fm.app_directory(b"\0")["errors"]
    for bad in (b"\0" * 10,):
        with pytest.raises(fm.PackageError) as e:
            fm.Package.from_bytes(bad)
        out.append(str(e.value))
    with pytest.raises(fm.PackageError) as e:
        fm.Package.load(tmp_path / "missing.fwsc")
    out.append(str(e.value))
    with pytest.raises(AssertionError) as e:
        fm.cleanup_plan([{"sector": 0x1000}])
    out.append(str(e.value))
    out += list(fm.LEVEL_TEXT.values())

    # restzone: gates, plans, reports, a real run against the mock FM-1 (with its log)
    for a in (0x93001, 0x4000, 0xA8000 + 0x1000):
        with pytest.raises(rz.Refused) as e:
            rz.gate(a)
        out.append(str(e.value))
    out.append(fm.NEVER_ERASE[0][2])
    for d in (b"\0", dumps["demo-v16-abgeschnitten.bin"], dumps["demo-sauber.bin"]):
        out.append(rz.make_plan(d, refs).reason)
    out.append(rz.make_plan(dumps["demo-sauber.bin"], [r for r in refs if r.family != "v16-layout"]).reason)
    fu = rz._unbricker()
    fu.setup_logging("test", tmp_path / "logs")
    grab = _Grab()
    fu.log.addHandler(grab)
    try:
        def mock(img):
            m = fu.MockUboot(image=bytes(img))
            return fu.Fm1Device("mock://fm1-uboot", m, io_max=512, mock=True)
        img = rz.demo_image()
        dev = mock(img)
        res = [rz.execute(dev, fu, tmp_path / "a", refs, plan_only=True)]
        only = {int(p["addr"], 16) for p in res[0]["planned"]}
        res.append(rz.execute(dev, fu, tmp_path / "a", refs, only=only - {0xA8000} | {0x93000}))
        res.append(rz.execute(dev, fu, tmp_path / "a", refs, plan_only=True))
        res.append(rz.execute(mock(img), fu, tmp_path / "b", refs, only={0x97000, 0x98000}, abort_after=1))
        res.append(rz.execute(mock(dumps["demo-v16-abgeschnitten.bin"]), fu, tmp_path / "c", refs, plan_only=True))
        res.append({"plan_only": False, "erased": [], "error": "x"})
        res.append({"plan_only": True, "mock": True, "planned": []})
        for r in res:
            rep(rz.report(r))
            out += [s["why"] for s in r.get("skipped", [])] + [r.get("refused", ""), r.get("error", "")]
        # rescuegate (the log of its run goes through the same unbricker log)
        out += [rg.ALTERNATIVE]
        for d in dumps.values():
            for v in (rg.repair_verdict(d, refs), rg.restore_verdict(d, refs), rg.restore_verdict(d, refs, heads)):
                out += [v.get("reason", ""), v.get("firmware", ""), v.get("basis", ""), v.get("head", "")]
        st_bad = bytearray(dumps["demo-sauber.bin"])
        st_bad[0x4000:0x93000] = b"\0" * (0x93000 - 0x4000)
        out += [rg.repair_verdict(bytes(st_bad), refs)["reason"], rg.repair_verdict(b"\0", refs)["reason"],
                rg.restore_verdict(b"\0", refs)["reason"]]
        v16 = next(r for r in refs if r.family == "v16-layout")
        fake = SimpleNamespace(**{**v16.__dict__, "head_sha256": fm.sha(dumps["demo-sauber.bin"][:fm.HEAD_END])})
        out.append(rg.restore_verdict(dumps["demo-sauber.bin"], [fake])["reason"])
    finally:
        fu.log.removeHandler(grab)
        fu.close_logging()
    out += grab.lines
    return [t for t in out if t]


def test_b_round_trip_german_is_exactly_the_original(tmp_path):
    with lang("de"):
        msgs = produce(tmp_path)
        assert len(msgs) > 120
        bad = [m for m in msgs if texts.tr(m) != m]
        assert not bad, bad[:5]
        assert texts.level_text("gelb") == "Mit Hinweisen"


def _english_ok(src: str, out: str) -> bool:
    """translated, and nothing German left (names of Melodee etc. are fine)"""
    return not i18n_check.looks_german(out)


def test_c_english_output_is_english_with_numbers_intact(tmp_path):
    with lang("de"):
        msgs = produce(tmp_path)
    with lang("en"):
        left = []
        for m in msgs:
            t = texts.tr(m)
            if i18n_check.looks_german(m) and not _english_ok(m, t):
                left.append((m, t))
            for tok in re.findall(r"0x[0-9A-F]{4,}|[0-9a-f]{12,}", m):     # addresses and checksums survive
                assert tok in t, (m, t)
        assert not left, "\n".join(f"{a!r}\n  -> {b!r}" for a, b in left[:8])


def test_c_samples_in_english():
    with lang("en"):
        assert texts.tr("In 3 Sektoren (0x97000–0x99FFF) liegen noch Reste von V16. V15 und Community-Firmwares "  # i18n: ignore
                        "schreiben dort nie hin und räumen sie nicht weg. Melodee 1.5.1 lief im Emulator damit genau "  # i18n: ignore
                        "wie auf sauberem Flash; für 1.6.0-rc.7 ist das nicht geprüft. Löschen kannst du sie im "  # i18n: ignore
                        "ROM-Bootmodus mit „V16-Reste aufräumen“ (Expertenmodus dieser Karte, am echten Gerät "  # i18n: ignore
                        "ungetestet) – oder liegen lassen.").startswith(  # i18n: ignore
            "Leftovers of V16 are still in 3 sectors (0x97000–0x99FFF).")
        assert texts.tr("1 Sektoren tragen noch V16-Bytes (0x97000–0x97FFF). Diese – und nur diese – würde "  # i18n: ignore
                        "„Aufräumen“ löschen.").startswith("1 sector still carries V16 bytes (0x97000–0x97FFF).")  # i18n: ignore
        assert texts.tr("Die Datei ist 1.000 Bytes groß – ein vollständiger FM-1-Dump hat genau 1 MiB "  # i18n: ignore
                        "(1.048.576 Bytes).") == ("The file is 1,000 bytes in size – a complete FM-1 dump has exactly "  # i18n: ignore
                                                  "1 MiB (1,048,576 bytes).")
        assert texts.tr("ABGELEHNT: Diese Option gibt es über die App nicht. Es wurde nichts geschrieben.") == \
            "REFUSED: This option is not available through the app. Nothing was written."  # i18n: ignore
        assert texts.tr("ERROR: ABGELEHNT (Suite-Prüfung der Sicherung): Die Sicherung hat nicht genau 1 MiB – so "  # i18n: ignore
                        "wird nichts geschrieben.") == ("ERROR: REFUSED (suite check of the backup): The backup is not "  # i18n: ignore
                                                        "exactly 1 MiB – so nothing is written.")
        assert texts.tr("  0xA7000 gelöscht und geprüft (teilweise)") == "  0xA7000 erased and checked (partial)"  # i18n: ignore
        assert texts.tr("Dateitabelle 0x04000: FEHLER: Prüfsumme (CRC) von app.bin stimmt nicht; kein Eintrag PRCT "  # i18n: ignore
                        "(Ende der Firmware), Aufbau ohne Vorlage, app.bin 581.200 Bytes") == (  # i18n: ignore
            "File table 0x04000: ERROR: checksum (CRC) of app.bin does not match; no entry PRCT (end of the firmware), "
            "layout without a reference, app.bin 581,200 bytes")
        lost = fm.describe_loss("FM-1_91060", "melodee", "FM-1_016")
        t = [texts.tr(x) for x in lost]
        assert "Prophet banks" in t[0] and "0x93000–0xA8FFF (22 sectors)" in t[0]
        assert "resources (factory sounds, font, Bluetooth program)" in t[1]
        assert "0xBA000–0xC9FFF" in t[1]


def test_unknown_text_is_shown_unchanged():
    with lang("en"):
        for s in ("", "Hallo Welt – völlig unbekannt.", "ERROR: device not found", "Sektor 0x12 hat"):  # i18n: ignore
            assert texts.tr(s) == s
        assert texts.tr(None) is None


def test_report_and_verdict_helpers():
    refs = fm.load_known() + fm.demo_refs()
    r = fm.check_dump(fm.demo_dumps()["demo-v16-reste.bin"], refs, factory_heads=fm.demo_heads())
    with lang("en"):
        t = texts.report(r)
        assert t.level == r.level and t.residue == r.residue and t is not r
        assert t.firmware == "Demo Melodee (simulation)" and t.title == "Flash dump check"
        assert any(x.startswith("Leftovers of V16") for _lv, x in t.findings)
        assert r.firmware == "Demo-Melodee (Simulation)"                 # the original stays untouched
        v = texts.verdict(rg.repair_verdict(b"\0", refs))
        assert v["reason"].startswith("The backup is not exactly 1 MiB") and v["ok"] is False
        res = texts.result({"refused": "Ohne bestätigte Sektorliste wird nichts gelöscht.",  # i18n: ignore
                            "skipped": [{"addr": "0x93000", "why": "V16-Rest, aber nicht bestätigt – bleibt."}]})  # i18n: ignore
        assert res["refused"] == "Without a confirmed sector list nothing is erased."
        assert res["skipped"][0] == {"addr": "0x93000", "why": "V16 leftover, but not confirmed – stays."}
        assert texts.level_text("rot") == "Not OK"


def test_helper_lines_of_the_runner_are_translated():
    from fm1companion.flash import uboot
    with lang("en"):
        assert uboot.helper_line("Schritt 3/5: Plan aus dieser Sicherung") == "Step 3/5: plan from this backup"  # i18n: ignore
        assert uboot.helper_line("Werkzeuge geprüft (3 Dateien, SHA-256) – starte die geprüfte Kopie als root.") == \
            "Tools checked (3 files, SHA-256) – starting the checked copy as root."  # i18n: ignore
        assert uboot.helper_line("Reading flash ...") == "Reading flash ..."
    with lang("de"):
        assert uboot.helper_line("Schritt 3/5: Plan aus dieser Sicherung") == "Schritt 3/5: Plan aus dieser Sicherung"  # i18n: ignore


def test_planner_loss_sentences_use_the_table():
    from fm1companion.flash import planner
    assert not hasattr(planner, "_loss_text")
    with lang("en"):
        lost = [texts.tr(x) for x in fm.describe_loss("FM-1_016", "", "FM-1_015")]
        assert lost[0].startswith("Everything you saved under V16")
        assert json.dumps(lost, ensure_ascii=False).count("V16") >= 3


_SAMPLES = {texts.HEX: "0x93000", texts.SHA: "abcdef0123456789", r"\d+": "3", r"[0-9]+": "3", ".*?": "",
            texts._FILES: ", app.bin 581.200 Bytes", texts._ALT: rg.ALTERNATIVE,
            r"Das entsteht .+?": "Das entsteht nur, wenn im ROM-Bootmodus der ganze Flash beschrieben wurde."}  # i18n: ignore


def _sample(rule: texts.Rule) -> str:
    """the German template with every variable part filled with a plausible value"""
    def fill(m):
        name = m.group(1)
        pat = rule.pat.get(name) or (r"[0-9]+" if name == rule.n else "")
        if pat in _SAMPLES:
            v = _SAMPLES[pat]
        elif pat:
            v = re.sub(r"\\(.)", r"\1", pat.split("|")[0])
        else:
            v = "Melodee 1.6.0"
        assert re.fullmatch(pat or ".+", v, re.S), (rule.de, name, v)
        return v
    return re.sub(r"\{(\w+)\}", fill, rule.de)


@pytest.mark.parametrize("rule", texts.RULES, ids=lambda r: r.de[:40])
def test_every_rule_round_trips_and_translates(rule):
    s = _sample(rule)
    assert texts.matches(s)
    with lang("de"):
        assert texts.tr(s) == s
    with lang("en"):
        t = texts.tr(s)
        assert t != s or rule.de == (rule.en if isinstance(rule.en, str) else rule.en[0])
        assert not i18n_check.looks_german(t), (s, t)
