# SPDX-License-Identifier: GPL-3.0-only
"""Sicherheitsbefunde S1–S3 (docs/RESEARCH-2026-11.md): Rettung Stufe 2 und Dump-Prüfung.

S1: „Einstellungen reparieren“ schreibt nie auf einen Flash mit V16- oder unbekanntem Aufbau.
S2: „Original-V15 zurückschreiben“ schreibt nie unter einem V16- oder unbekannten Bootkopf.
S3: ein Werks-Abbild (gültige Dateitabelle, andere Bytes als das V15-Paket) heißt nicht „beschädigt“.
Alles gegen das Mock-FM-1 des Unbrickers; geprüft wird an dessen Löschen-/Schreiben-Protokoll (MockUboot.ops).
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fm1companion.flash import flashmodel as m, rescuegate as g, uboot  # noqa: E402

V15 = Path("/tmp/fm1_open/sdk/firmware/FM-1_015.fwsc")
need_v15 = pytest.mark.skipif(not V15.exists(), reason="Original-V15 fehlt")
FU = g._unbricker()


# ----------------------------------------------------------------------------- Hilfen
def _dump_of(flash: bytes) -> bytearray:
    d = bytearray(b"\xff" * m.FLASH_SIZE)
    d[:len(flash)] = flash
    return d


def _with_user_data(d: bytearray, vm_start=0x93000) -> bytearray:
    """Einstellungen im VM-Bereich, damit repair-settings wirklich etwas zu tun hätte"""
    d[vm_start:vm_start + 0x3000] = hashlib.sha256(b"vm").digest() * (0x3000 // 32)
    d[0xFC000:0xFD000] = hashlib.sha256(b"glob").digest() * (0x1000 // 32)
    return d


def _edit_directory(d: bytearray, fn, area_len=None) -> bytearray:
    """Dateitabelle bei 0x4000 entschlüsseln, fn(plain) ändern lassen, Kopf-CRCs neu rechnen, wieder verschlüsseln
    (die Stromchiffre hängt nur von der Position ab und ist ihr eigenes Inverses)"""
    n = area_len or 0x200
    plain = bytearray(m._decrypt_area(bytes(d), m.APP_START, n))
    fn(plain)
    for i in range(16):
        e = plain[32 * i:32 * (i + 1)]
        if e == b"\xff" * 32 or len(e) < 32:
            break
        struct.pack_into("<H", plain, 32 * i, m.crc16(e[2:32]))
    d[m.APP_START:m.APP_START + n] = m._decrypt_area(bytes(plain), 0, n)
    return d


def _entry(plain, name: bytes):
    for i in range(16):
        if plain[32 * i + 16:32 * i + 16 + len(name)] == name:
            return 32 * i
    raise KeyError(name)


def v16_layout_dump(v15_flash: bytes) -> bytes:
    """Dump, dessen eigene (CRC-gültige) Dateitabelle den V16-Aufbau sagt: PRCT bis 0xA9000, VM ab 0xBA000, SEQ64 –
    so wie V16 (FM-1_016) ihn hat. Die Bytes sind sonst V15 – darum kennt kein Steckbrief sie."""
    d = _with_user_data(_dump_of(v15_flash), 0xBA000)

    def fn(p):
        o = _entry(p, b"PRCT")
        struct.pack_into("<I", p, o + 8, 0xA9000)
        o = _entry(p, b"VM\0")
        struct.pack_into("<II", p, o + 4, 0xBA000, 0x20000)
        o = _entry(p, b"USR\0")
        p[o + 16:o + 32] = b"SEQ64".ljust(16, b"\0")
    return bytes(_edit_directory(d, fn))


def factory_like_dump(v15_flash: bytes) -> bytes:
    """wie ein Werksgerät: andere Bytes in app.bin als das V15-Paket, Dateitabelle mit passender CRC (gültig)"""
    d = _with_user_data(_dump_of(v15_flash))
    area = 0x8E25B
    plain = bytearray(m._decrypt_area(bytes(d), m.APP_START, area))
    for off in (0x1000, 0x20000, 0x50000):
        plain[off] ^= 0x5A
    o = _entry(plain, b"app.bin")
    a, n = struct.unpack_from("<II", plain, o + 4)
    struct.pack_into("<H", plain, o + 2, m._crc16_fast(plain[a:a + n]))
    struct.pack_into("<H", plain, o, m.crc16(plain[o + 2:o + 32]))
    d[m.APP_START:m.APP_START + area] = m._decrypt_area(bytes(plain), 0, area)
    return bytes(d)


def factory_dump(tmp_path) -> Path:
    """sauberer Werks-Dump für repair-settings: Sektor 0x93000 beginnt mit 55 AA AA 55, Rest der Fenster leer"""
    f = bytearray(b"\xff" * m.FLASH_SIZE)
    f[0x93000:0x93004] = b"\x55\xAA\xAA\x55"
    f[0x93004:0x93100] = b"\x01" * 0xFC
    p = tmp_path / "werk.bin"
    p.write_bytes(bytes(f))
    return p


def run_gate(argv, image: bytes, tmp_path):
    """rescuegate.main gegen ein Mock-FM-1 mit diesem Flash -> (rc, Ergebnis, Mock)"""
    mock = FU.MockUboot(image=image)
    dev = FU.Fm1Device("mock://fm1-uboot", mock, io_max=mock.usb_buffer_size(), mock=True)
    res = tmp_path / "res.json"
    rc = g.main(argv + ["--result", str(res), "--dry-run", "--log-dir", str(tmp_path / "logs")], device=dev)
    return rc, json.loads(res.read_text("utf-8")), mock


def writes(mock) -> list:
    return list(mock.ops)                             # MockUboot.ops: nur erase_sector / erase_block / write


@pytest.fixture
def incwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                       # der Unbricker legt backups/ im Arbeitsordner an
    return tmp_path


# ----------------------------------------------------------------------------- Struktur (Dateitabelle)
@need_v15
def test_app_directory_v15_is_valid_and_v15_layout():
    p = m.Package.load(V15)
    st = m.app_directory(bytes(_dump_of(p.flash)))
    assert st["ok"] and st["prct_end"] == 0x93000 and st["vm"][0] == 0x93000 and not st["seq64"]
    assert [f["name"] for f in st["files"]] == ["app.bin", "cfg_tool.bin"] and all(f["crc_ok"] for f in st["files"])
    assert m.directory_layout(st) == "v15-layout"
    assert m.directory_layout(m.app_directory(v16_layout_dump(p.flash))) == "v16-layout"
    assert m.app_directory(bytes(m.FLASH_SIZE * b"\xff"))["found"] is False


# ----------------------------------------------------------------------------- S3
@need_v15
def test_s3_factory_image_is_not_called_damaged():
    p = m.Package.load(V15)
    rep = m.check_dump(factory_like_dump(p.flash), packages=[p])
    texts = " ".join(t for _, t in rep.findings)
    assert rep.level != m.RED, rep.findings
    assert "beschädigt" not in texts and "neu aufspielen" not in texts.replace("Nichts neu aufspielen", "")
    assert "Werks-Abbild" in texts and "kein Fehler" in texts
    assert any("Abweichende Sektoren" in d for d in rep.details)


@need_v15
def test_s3_known_factory_hash_is_recognised(monkeypatch):
    p = m.Package.load(V15)
    d = factory_like_dump(p.flash)
    monkeypatch.setitem(m.FACTORY_APPS, m.sha(d[m.APP_START:m.V15_END]), "Werks-Abbild (Test)")
    rep = m.check_dump(d, packages=[p])
    assert rep.firmware == "Werks-Abbild (Test)" and rep.level == m.YELLOW
    assert "8c1d13a5934eb3c70a5fec644577618a31b2a96c8748a7b692c506a86505dbb1" in m.FACTORY_APPS


@need_v15
def test_s3_real_structural_error_is_still_damaged():
    p = m.Package.load(V15)
    d = _dump_of(p.flash)
    d[0x30000] ^= 1                                   # ein Bit in app.bin: Datei-CRC stimmt nicht mehr
    rep = m.check_dump(bytes(d), packages=[p])
    assert rep.level == m.RED and any("CRC" in t and "beschädigt" in t for _, t in rep.findings)
    d = _dump_of(p.flash)
    d[0x4000:0x5000] = b"\xff" * 0x1000               # abgebrochen: Tabelle weg, Rest fast gleich V15
    assert m.check_dump(bytes(d), packages=[p]).level == m.RED


@need_v15
def test_s3_unknown_v16_layout_firmware_not_reported_as_residue():
    p = m.Package.load(V15)
    rep = m.check_dump(v16_layout_dump(p.flash), packages=[p])
    assert not rep.residue and not any("Reste von V16" in t for _, t in rep.findings)


# ----------------------------------------------------------------------------- S1: repair-settings
@need_v15
def test_s1_repair_refused_on_v16_layout_dump(incwd):
    p = m.Package.load(V15)
    rc, res, mock = run_gate(["repair-settings", "--factory", str(factory_dump(incwd)), "--yes"],
                             v16_layout_dump(p.flash), incwd)
    assert rc == g.EXIT_REFUSED and res["checked"] and not res["ok"]
    assert "V16-Aufbau" in res["refused"] and "nichts geschrieben" in res["refused"]
    assert writes(mock) == [] and mock.lowest_touched() is None
    assert list(incwd.glob("backups/*/full-flash-before.bin"))      # Sicherung liegt trotzdem
    # Beleg für den Befund: der Unbricker allein hätte in 0x93000–0xA8FFF (V16: noch Firmware) geschrieben
    mock = FU.MockUboot(image=v16_layout_dump(p.flash))
    dev = FU.Fm1Device("mock://fm1-uboot", mock, io_max=mock.usb_buffer_size(), mock=True)
    assert FU.main(["repair-settings", "--factory", str(factory_dump(incwd)), "--yes", "--dry-run",
                    "--log-dir", str(incwd / "logs")], device=dev) == 0
    assert any(0x93000 <= op[1] < 0xA9000 for op in mock.ops)


def test_s1_repair_refused_on_known_v16_and_truncated_v16(incwd):
    refs = m.demo_refs()
    v16 = m._demo_bytes("demo-v16", 0xA9000)
    d = bytearray(m.demo_dumps()["demo-sauber.bin"])
    d[m.APP_START:0xA9000] = v16[m.APP_START:]
    v = g.repair_verdict(bytes(d), refs)
    assert not v["ok"] and "V16-Aufbau" in v["reason"]
    v = g.repair_verdict(m.demo_dumps()["demo-v16-abgeschnitten.bin"], refs)
    assert not v["ok"] and "abgeschnitten" in v["reason"]
    v = g.repair_verdict(bytes(m.FLASH_SIZE * b"\x00"), refs)            # unbekannt, keine Tabelle
    assert not v["ok"] and "nichts" in v["reason"]


@need_v15
def test_s1_repair_refused_on_damaged_or_v14(incwd):
    p = m.Package.load(V15)
    d = _dump_of(p.flash)
    d[0x30000] ^= 1
    v = g.repair_verdict(bytes(d), m.load_known())
    assert not v["ok"] and "beschädigt" in v["reason"]

    def v14(pl):                                      # V13/V14: Firmware und VM bis/ab 0x94000 [Paket FM-1-RE]
        struct.pack_into("<I", pl, _entry(pl, b"PRCT") + 8, 0x94000)
        struct.pack_into("<II", pl, _entry(pl, b"VM\0") + 4, 0x94000, 0x55000)
    d = _edit_directory(_dump_of(p.flash), v14)
    st = m.app_directory(bytes(d))
    assert st["ok"] and m.directory_layout(st) == ""
    v = g.repair_verdict(bytes(d), m.load_known())
    assert not v["ok"] and "nicht gebaut" in v["reason"]


@need_v15
def test_s1_repair_allowed_on_v15_and_factory_image(incwd):
    p = m.Package.load(V15)
    assert g.repair_verdict(bytes(_dump_of(p.flash)), m.load_known())["ok"]
    v = g.repair_verdict(factory_like_dump(p.flash), m.load_known())
    assert v["ok"] and "Dateitabelle" in v["basis"]
    rc, res, mock = run_gate(["repair-settings", "--factory", str(factory_dump(incwd)), "--yes"],
                             factory_like_dump(p.flash), incwd)
    assert rc == 0 and res["ok"], res
    touched = sorted({op[1] for op in mock.ops if op[0] != "read"})
    assert touched and min(touched) >= 0x93000                        # nie Bootkopf oder Firmware


def test_s1_done_text_never_claims_untouched_firmware_without_check():
    from fm1companion.ui.pages import recovery
    t = recovery.repair_done_text({"firmware": "FM-1_015", "basis": "bekanntes Paket mit V15-Aufbau"})
    assert "geprüft" in t and "0x0000–0x92FFF" in t and "V15-Aufbau" in t
    assert "Simulation" in recovery.repair_done_text({}, sim=True)


# ----------------------------------------------------------------------------- S2: restore unter fremdem Bootkopf
def _v16_head_refs(head: bytes):
    refs = m.load_known()
    for k in refs:
        if k.family == "v16-layout":
            k.head_sha256 = m.sha(head)
    return refs


@need_v15
def test_s2_v16_head_v15_not_written(incwd):
    head = hashlib.sha256(b"v16-kopf").digest() * (m.HEAD_END // 32)
    d = bytearray(FU.synthetic_flash())
    d[:m.HEAD_END] = head
    v = g.restore_verdict(bytes(d), _v16_head_refs(head))
    assert not v["ok"] and "V16" in v["reason"] and "M-Flasher" in v["reason"]
    # durch den echten Lauf: Steckbrief-Datei mit diesem Kopf als V16-Kopf
    raw = json.loads(m.DATA_FILE.read_text("utf-8"))
    for e in raw["packages"]:
        if e["family"] == "v16-layout":
            e["head_sha256"] = m.sha(head)
    refs = incwd / "refs.json"
    refs.write_text(json.dumps(raw), "utf-8")
    rc, res, mock = run_gate(["restore", str(V15), "--yes", "--verify-v15", "--refs", str(refs)], bytes(d), incwd)
    assert rc == g.EXIT_REFUSED and "V16" in res["refused"] and writes(mock) == []
    assert mock.lowest_touched() is None
    # Beleg für den Befund: der Unbricker allein schreibt V15 unter jedem Kopf
    mock = FU.MockUboot(image=bytes(d))
    dev = FU.Fm1Device("mock://fm1-uboot", mock, io_max=mock.usb_buffer_size(), mock=True)
    assert FU.main(["restore", str(V15), "--yes", "--verify-v15", "--dry-run", "--log-dir", str(incwd / "logs")],
                   device=dev) == 0 and mock.lowest_touched() == 0x4000


@need_v15
def test_s2_unknown_head_refused_factory_head_written(incwd):
    d = bytearray(FU.synthetic_flash())               # zufälliger Kopf = unbekannt
    rc, res, mock = run_gate(["restore", str(V15), "--yes", "--verify-v15"], bytes(d), incwd)
    assert rc == g.EXIT_REFUSED and "kennt die App nicht" in res["refused"] and mock.lowest_touched() is None
    p = m.Package.load(V15)
    d[:m.HEAD_END] = p.flash[:m.HEAD_END]             # Kopf ab Werk (= V15-Paket)
    assert m.sha(bytes(d[:m.HEAD_END])) == m.FACTORY_HEAD_SHA256
    rc, res, mock = run_gate(["restore", str(V15), "--yes", "--verify-v15"], bytes(d), incwd)
    assert rc == 0 and res["ok"] and mock.lowest_touched() == 0x4000
    assert bytes(mock.flash[m.APP_START:m.V15_END]) == p.flash[m.APP_START:]
    assert bytes(mock.flash[:m.HEAD_END]) == p.flash[:m.HEAD_END]      # Kopf nie angefasst


def test_gate_refuses_dangerous_options(tmp_path):
    for extra in (["--i-know"], ["--force-write"]):
        r = g.main(["restore", "x.fwsc", "--yes", "--verify-v15", "--result", str(tmp_path / "r.json")] + extra)
        assert r == g.EXIT_REFUSED
    assert g.main(["restore", "x.fwsc", "--yes", "--result", str(tmp_path / "r.json")]) == g.EXIT_REFUSED
    assert g.main(["repair-settings", "--factory", "f", "--yes", "--demo-refs",
                   "--result", str(tmp_path / "r.json")]) == g.EXIT_REFUSED      # Demo nur in der Simulation


# ----------------------------------------------------------------------------- Einbindung (Runner, root-Kopie)
def test_runner_routes_restore_and_repair_through_gate(tmp_path):
    from fm1companion.flash import uboot_manifest as M
    r = uboot.Runner(dry_run=False, cwd=tmp_path, root_cmd=["pkexec"])
    for argv in (r.argv("restore", "x.fwsc", script=uboot.GATE_SCRIPT),
                 r.argv("repair-settings", "--factory", "f.bin", script=uboot.GATE_SCRIPT)):
        spec = json.loads(argv[-1])
        assert spec["script"] == "rescuegate.py" and set(spec["own"]) == set(M.OWN)
    calls = []
    r.run = lambda cmd, *a, **k: calls.append((cmd, a, k)) or 0
    r.repair_settings("f.bin")
    with pytest.raises(RuntimeError):                  # V16-Paket: wie bisher schon vorher abgelehnt
        r.restore(str(tmp_path / "fehlt.fwsc"))
    assert calls and all(k.get("script") == uboot.GATE_SCRIPT for _c, _a, k in calls)
    assert "--result" in calls[0][1]


def test_root_helper_runs_gate_copy_in_simulation(tmp_path):
    """der echte ROOT_HELPER kopiert Unbricker + rescuegate + flashmodel + Steckbriefe, prüft und startet"""
    jl = tmp_path / "jl"
    jl.mkdir()
    (jl / "x.py").write_text("")
    img = tmp_path / "img.bin"
    img.write_bytes(m.demo_dumps()["demo-v16-abgeschnitten.bin"])
    spec = uboot.root_spec(uboot.GATE_SCRIPT, ["repair-settings", "--factory", str(factory_dump(tmp_path)), "--yes",
                                               "--result", str(tmp_path / "r.json"), "--dry-run", "--mock-image",
                                               str(img), "--demo-refs"], tmp_path, tmp_path / "logs")
    spec["jl_dir"], spec["jl"] = str(jl), {"x.py": hashlib.sha256(b"").hexdigest()}
    r = subprocess.run([sys.executable, "-I", "-c", uboot.ROOT_HELPER, json.dumps(spec)], capture_output=True,
                       text=True)
    assert "geprüft" in r.stdout, r.stdout + r.stderr
    assert r.returncode == g.EXIT_REFUSED, r.stdout + r.stderr
    assert "abgeschnitten" in json.loads((tmp_path / "r.json").read_text())["refused"]


@need_v15
def test_runner_dry_run_restore_passes_gate(tmp_path):
    logs = []
    r = uboot.Runner(dry_run=True, log=logs.append, cwd=tmp_path / "r")
    assert r.restore(str(V15)) == 0
    v = r.last_verdict()
    assert v["ok"] and v["checked"] and "Simulation" in v["verdict"]["head"]
    assert any("Suite-Prüfung der Sicherung: OK" in line for line in logs)


def test_loader_identity_fm1_open():
    from fm1companion.flash import engine, package as pk
    ident = "ota-FM1C_0123456789abcdef"
    assert pk.OPEN_LOADER_RE.match(ident) and not pk.STOCK_LOADER_RE.match(ident)
    assert not pk.LoaderRule("stock", "FM-1_015")(ident) and not pk.LoaderRule("felucca", "FM-1_900")(ident)
    assert "FM1 Open" in engine.loader_hint(ident)
