# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests of the MIDI area: decoder, clock, ALSA parser, SF2 writer, FluidSynth commands."""
from __future__ import annotations

import math
import struct
import sys
import time
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fm1companion import alsaseq, clock, miditools as M, sf2, soundmodule as SM  # noqa: E402
from fm1companion.protocol import frame  # noqa: E402


# ------------------------------------------------------------ decoder ---
def test_notes_cc_prog_bend():
    d = M.decode([0x94, 60, 100])
    assert (d.kind, d.type, d.channel) == ("note", "Note an", 5) and "C4 (60)" in d.meaning
    assert M.decode([0x90, 61, 0]).type == "Note aus"               # Velocity 0
    assert "C#4" in M.decode([0x80, 61, 0]).meaning
    assert "Lautstärke" in M.decode([0xB0, 7, 99]).meaning
    assert "Spur-Lautstärke" in M.decode([0xB0, 7, 99], firmware="melodee").meaning
    assert "KNOB1" in M.decode([0xB3, 80, 1], firmware="melodee").meaning
    assert "Acoustic Grand" in M.decode([0xC0, 0]).meaning
    assert M.decode([0xE0, 0, 64]).meaning.startswith("+0")
    assert M.decode([0xE0, 0, 0]).meaning == "-8192"
    assert M.decode([0xF8]).kind == "clock" and M.decode([0xFA]).kind == "transport"
    assert M.decode([0xFE]).kind == "sense"
    assert M.note_name(21) == "A0" and M.note_name(127) == "G9"


def test_felucca_names_and_args():
    d = M.decode(frame(31, [4, 83, *M.v14_enc(5)]), "out")
    assert d.kind == "sysex" and "TRACK_PARAM" in d.meaning and "Spur 5" in d.meaning and "= 5" in d.meaning
    assert "INFO" in M.decode(frame(1)).meaning
    info = frame(1, [*b"MELODEE v1.6", 0, 2])
    assert "MELODEE v1.6" in M.decode(info, "in").meaning
    assert "(Meldung)" in M.decode(frame(23, [0, 1, *M.v14_enc(-3)]), "in").meaning
    assert "= -3" in M.decode(frame(23, [0, 1, *M.v14_enc(-3)]), "in").meaning
    assert "BACKUP_LIST" in M.decode(frame(65)).meaning
    assert "LIBRARY" in M.decode(frame(80, [0, 0, 0, 16])).meaning       # only in the editor table
    assert M.v14_dec(*M.v14_enc(0)) == 0 and M.v14_enc(0) == [0, 64]


def test_dx7_mvave_universal():
    assert "1 Voice" in M.decode([0xF0, 0x43, 0x00, 0x00, 0x01, 0x1B] + [0] * 155 + [0, 0xF7]).meaning
    assert "32 Voices" in M.decode([0xF0, 0x43, 0x02, 0x09, 0x20, 0x00] + [0] * 10 + [0xF7]).meaning
    assert "Voice-Parameter 129" in M.decode([0xF0, 0x43, 0x10, 0x01, 0x01, 7, 0xF7]).meaning
    assert "Update-Modus" in M.decode([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7]).meaning
    assert "Kennung" in M.decode([0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7]).meaning
    # data request of the device (15 bytes unpacked, channel 00 59 30)
    from fm1companion.miditools import unpack_bits
    body = bytearray([0x00, 0x59, 0x30, 0, 0, 0, 0]) + (0x1234).to_bytes(4, "little") + bytes([0x10, 0, 0])
    body.append(~sum(body[6:]) & 0xFF)
    out, acc, nb = [], 0, 0
    for b in body:
        acc |= b << nb
        nb += 8
        while nb >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            nb -= 7
    if nb:
        out.append(acc & 0x7F)
    assert unpack_bits(out)[:15] == bytes(body)
    assert "0x1234" in M.decode([0xF0, *out, 0xF7]).meaning
    assert "Identity Request" in M.decode([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]).meaning
    assert "Preset-Datei" in M.decode([0xF0, 0x7D, 0x4D, 0x4C, 1, 0, 0xF7]).meaning


def test_mvave_keys_named():
    """all variants of the M-VAVE control SysEx get a name that says what happens on the device."""
    assert "Update-Modus" in M.decode([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7]).meaning
    assert "Bootmodus" in M.decode([0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7]).meaning
    assert "22 24 35" in M.decode([0xF0, 0x22, 0x24, 0x35, 0x01, 0xF7]).meaning
    assert "Kennung" in M.decode([0xF0, 0x00, 0x32, 0x45, 0, 0, 0, 0x40, 0x7F, 0xF7]).meaning


def test_all_editor_commands_named():
    """decoder against the CMD table of the Melodee editor (web/editor.html): every command has a name."""
    import re
    from pathlib import Path
    html = Path("/tmp/melodee/web/editor.html")
    if not html.exists():
        pytest.skip("Melodee sources missing")
    m = re.search(r"const CMD = \{(.*?)\};", html.read_text("utf-8"), re.S)
    table = dict(re.findall(r"([A-Z0-9_]+)\s*:\s*(\d+)", m.group(1)))
    assert len(table) >= 50
    for name, num in table.items():
        assert M.FELUCCA_CMDS.get(int(num)) == name, (name, num)
    for num in table.values():
        d = M.decode([0xF0, 0x7D, 0x46, 0x4C, int(num), 0xF7], "in")
        assert "Befehl" not in d.meaning


def test_long_sysex_decodes_and_hex_is_capped():
    big = [0xF0, 0x7D, 0x46, 0x4C, 66] + [0x55] * 600 + [0xF7]
    d = M.decode(big)
    assert d.kind == "sysex" and "BACKUP_GET" in d.meaning and "606 Bytes" in d.data and "…" in d.data
    assert "[600 Bytes]" in d.meaning
    raw = M.decode(list(range(0x00, 0x80)))         # data bytes without status
    assert raw.kind == "other" and raw.meaning


def test_clock_meter_bpm():
    m = M.ClockMeter()
    t = 0.0
    for _ in range(48):
        m.feed(0xF8, t)
        t += 60 / 125 / 24
    assert abs(m.bpm - 125) < 0.01 and m.jitter_ms < 0.01
    m.feed(0xF8, t + 1.0)                      # pause -> measure again
    assert m.bpm is None


def test_eventlog_and_export():
    log = M.EventLog(10)
    for i in range(15):
        log.add([0x90, 60 + i, 100], "in", "FM-1")
    got = log.take()
    assert len(got) == 10 and len(log.events) == 10
    csv = M.export_text(log.events, "csv")
    assert csv.splitlines()[0].startswith("Zeit") and "Note an" in csv
    assert "C5" in M.export_text(log.events, "txt")


# ------------------------------------------------------------- Clock ---
def test_tap_tempo():
    tt = clock.TapTempo()
    assert tt.tap(0.0) is None
    for k in range(1, 5):
        bpm = tt.tap(k * 0.5)
    assert abs(bpm - 120) < 1e-6


def test_swing_offsets():
    assert clock.swing_offset(3, 120, 0.3) == 0
    assert clock.swing_offset(6, 120, 0.3) > clock.swing_offset(9, 120, 0.3) > 0


def test_clock_scheduler_jitter():
    got = []
    cm = clock.ClockMaster(lambda m: got.append((time.perf_counter(), m[0])), bpm=240)
    cm.start()
    time.sleep(1.2)
    cm.bpm = 300
    time.sleep(0.4)
    cm.stop()
    pulses = [t for t, b in got if b == clock.CLOCK]
    assert got[0][1] == clock.START and got[-1][1] == clock.STOP
    assert len(pulses) > 100
    st = cm.jitter_stats()
    print(f"\nClock jitter in the container: mean {st['mean_ms']:.3f} ms, p95 {st['p95_ms']:.3f} ms, max {st['max_ms']:.3f} ms")
    assert st["p95_ms"] < 2.0
    # drift-free: 1.2 s at 240 BPM = 115 pulses (±3)
    first_part = [t for t in pulses if t - pulses[0] < 1.2]
    assert abs(len(first_part) - 1.2 / clock.pulse_interval(240)) < 4


# -------------------------------------------------------------- ALSA ---
PROC_FIXTURE = '''Client info
  cur  clients : 6
  peak clients : 7
  max  clients : 192

Client   0 : "System" [Kernel Legacy]
  Port   0 : "Timer" (Rwe-)
  Port   1 : "Announce" (R-e-)
    Connecting To: 128:0, 129:0
Client  14 : "Midi Through" [Kernel Legacy]
  Port   0 : "Midi Through Port-0" (RWe-)
Client  20 : "Melodee" [Kernel Legacy]
  Port   0 : "Melodee MIDI 1" (RWeX)
    Connecting To: 129:0, 130:0[real:0]
    Connected From: 128:0
Client  24 : "Minilab3" [Kernel Legacy]
  Port   0 : "Minilab3 MIDI" (RWeX)
  Port   1 : "Minilab3 DIN THRU" (-We-)
Client 128 : "RtMidiOut Client" [User Legacy]
  Port   0 : "RtMidi Output" (R-e-)
    Connecting To: 20:0
Client 129 : "RtMidiIn Client" [User Legacy]
  Port   0 : "RtMidi Input" (-We-)
    Connected From: 0:1, 20:0
Client 130 : "FM-1 Klangerzeuger" [User Legacy]
  Port   0 : "FM-1 Klangerzeuger Port 0" (-We-)
    Connected From: 20:0
'''

ACONNECT_FIXTURE = '''client 0: 'System' [type=kernel]
    0 'Timer           '
    1 'Announce        '
	Connecting To: 128:0
client 14: 'Midi Through' [type=kernel]
    0 'Midi Through Port-0'
client 20: 'Melodee' [type=kernel,card=1]
    0 'Melodee MIDI 1  '
	Connecting To: 129:0, 130:0
	Connected From: 128:0
client 130: 'FLUID Synth (4242)' [type=user,pid=4242]
    0 'Synth input port (4242:0)'
	Connected From: 20:0
'''


def test_parse_proc():
    ports = alsaseq.parse_proc(PROC_FIXTURE)
    by = {p.addr: p for p in ports}
    assert set(by) >= {"0:0", "20:0", "24:1", "130:0"}
    assert by["20:0"].readable and by["20:0"].writable and by["20:0"].kernel
    assert not by["24:1"].readable and by["24:1"].writable
    assert by["20:0"].to == ["129:0", "130:0"] and by["20:0"].frm == ["128:0"]
    assert by["128:0"].app and by["0:1"].system and not by["24:0"].app
    con = alsaseq.connections(ports)
    assert ("20:0", "130:0") in con and ("128:0", "20:0") in con
    assert alsaseq.find(ports, "melodee", readable=True).addr == "20:0"


def test_parse_aconnect():
    ports = alsaseq.parse_aconnect(ACONNECT_FIXTURE, readable={"20:0", "0:1"}, writable={"20:0", "130:0"})
    by = {p.addr: p for p in ports}
    assert by["20:0"].name == "Melodee MIDI 1" and by["0:0"].name == "Timer"
    assert by["130:0"].client_name == "FLUID Synth (4242)" and by["130:0"].frm == ["20:0"]
    assert not by["130:0"].readable and by["130:0"].writable


def test_connect_cmd_and_presets():
    assert alsaseq.connect_cmd("24:0", "20:0")[-2:] == ["24:0", "20:0"]
    assert alsaseq.connect_cmd("24:0", "20:0", True)[1:] == ["-d", "24:0", "20:0"]
    with pytest.raises(ValueError):
        alsaseq.connect_cmd("24:0; rm", "20:0")
    ports = alsaseq.parse_proc(PROC_FIXTURE)
    pr = alsaseq.capture("Test", ports, {("24:0", "20:0"), ("128:0", "20:0")})
    assert pr.pairs == [("Minilab3: Minilab3 MIDI", "Melodee: Melodee MIDI 1")]   # not the app connection
    # after a restart other client numbers: resolve again by name
    moved = PROC_FIXTURE.replace("Client  24", "Client  28").replace("Client  20", "Client  32")
    pairs, missing = alsaseq.resolve(alsaseq.Preset.from_json(pr.to_json()), alsaseq.parse_proc(moved))
    assert pairs == [("28:0", "32:0")] and not missing


# --------------------------------------------------------------- SF2 ---
def parse_sf2(data: bytes) -> dict:
    """independent mini parser: RIFF tree + pdta tables."""
    assert data[:4] == b"RIFF" and data[8:12] == b"sfbk"
    assert struct.unpack_from("<I", data, 4)[0] == len(data) - 8
    out = {}

    def walk(off, end):
        while off < end:
            cid = data[off:off + 4]
            size = struct.unpack_from("<I", data, off + 4)[0]
            body = data[off + 8:off + 8 + size]
            if cid == b"LIST":
                out.setdefault("lists", []).append(body[:4])
                walk(off + 12, off + 8 + size)
            else:
                out[cid.decode()] = body
            off += 8 + size + (size & 1)
    walk(12, len(data))
    rec = {"phdr": 38, "pbag": 4, "pmod": 10, "pgen": 4, "inst": 22, "ibag": 4, "imod": 10, "igen": 4, "shdr": 46}
    for k, n in rec.items():
        assert len(out[k]) % n == 0, k
        out[k + "_n"] = len(out[k]) // n
    return out


def _bank():
    b = sf2.Bank("Test Kit")
    for i in range(3):
        b.samples.append(sf2.Sample(f"s{i}", [int(8000 * math.sin(t / (3 + i))) for t in range(500 + i)], 22050, 60 + i))
    b.presets.append(sf2.SfPreset("KIT", [sf2.SfZone(i, 36 + i, 36 + i, 36 + i) for i in range(3)]))
    return b


def test_sf2_structure():
    data = sf2.build(_bank())
    sf = parse_sf2(data)
    assert sf["lists"] == [b"INFO", b"sdta", b"pdta"]
    assert sf["ifil"] == struct.pack("<HH", 2, 1) and sf["INAM"].rstrip(b"\0") == b"Test Kit"
    assert sf["phdr_n"] == 2 and sf["inst_n"] == 2 and sf["shdr_n"] == 4 and sf["ibag_n"] == 4
    assert sf["pmod_n"] == 1 and sf["imod_n"] == 1
    # samples: length + 46 zeros in between
    n = [500, 501, 502]
    assert len(sf["smpl"]) == 2 * (sum(n) + 3 * 46)
    s1 = struct.unpack_from("<20sIIIIIBbHH", sf["shdr"], 46)
    assert s1[1] == 546 and s1[2] == 546 + 501 and s1[5] == 22050 and s1[6] == 61
    # zone 2: keyRange first, sampleID last
    gens = [struct.unpack_from("<HH", sf["igen"], i * 4) for i in range(sf["igen_n"])]
    zone2 = gens[10:15]
    assert zone2[0] == (43, (38 << 8) | 38) and zone2[-1] == (53, 2)
    assert struct.unpack_from("<HH", sf["pgen"], 0) == (41, 0)
    eop = struct.unpack_from("<20sHHH", sf["phdr"], 38)
    assert eop[0].rstrip(b"\0") == b"EOP" and eop[3] == 1


def test_sf2_from_folder_and_kit(tmp_path):
    for i, f0 in enumerate((60, 200, 900)):
        with wave.open(str(tmp_path / f"{i} hit.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * f0 * t / 44100)))
                                   for t in range(2000)))
    b = sf2.bank_from_folder(tmp_path)
    assert [k for k, _ in sf2.drum_map(b)] == [36, 37, 38] and b.samples[0].rate == 44100
    out = sf2.write(b, tmp_path / "out" / "kit.sf2")
    assert parse_sf2(out.read_bytes())["phdr_n"] == 2
    from fm1companion.samples import Kit, Zone
    kit = Kit(name="POKIT", mode="keys", zones=[Zone(str(tmp_path / "0 hit.wav"), root=48),
                                                   Zone(str(tmp_path / "1 hit.wav"), root=72)])
    kb = sf2.bank_from_kit(kit)
    z = kb.presets[0].zones
    assert (z[0].lo, z[0].hi, z[1].lo, z[1].hi) == (0, 60, 61, 127) and not kb.presets[0].one_shot
    parse_sf2(sf2.build(kb))
    try:                                        # cross-check with sf2utils if available (no runtime dependency)
        from sf2utils.sf2parse import Sf2File
    except ImportError:
        return
    with open(out, "rb") as f:
        sf = Sf2File(f)
    assert len(sf.samples) >= 3 and sf.presets[0].name == b.name[:19]


# ---------------------------------------------------------- FluidSynth ---
def test_fluid_command_and_shell():
    cfg = SM.Config(soundfont="/usr/share/sounds/sf2/FluidR3_GM.sf2", latency="niedrig", gain=0.8, polyphony=64)
    cmd = SM.build_command("/usr/bin/fluidsynth", cfg, "pipewire")
    assert cmd[:5] == ["/usr/bin/fluidsynth", "-a", "pipewire", "-m", "alsa_seq"]
    assert "-i" not in cmd and "-s" not in cmd
    assert cmd[cmd.index("-z") + 1] == "64" and cmd[cmd.index("-c") + 1] == "2" and cmd[-1].endswith(".sf2")
    assert "synth.polyphony=64" in cmd and cmd[cmd.index("-p") + 1] == SM.CLIENT_NAME
    assert SM.parse_drivers("-a options (audio driver):\n   alsa, file, jack, pipewire, pulseaudio\n") == \
        ["alsa", "file", "jack", "pipewire", "pulseaudio"]
    assert SM.pick_driver(["alsa", "pulseaudio"]) == "pulseaudio"
    assert SM.channel_commands(SM.Channel(5, program=33, volume=90))[:2] == ["select 4 1 0 33", "cc 4 7 90"]
    r = SM.router_commands([5, 6])
    assert r[0] == "router_clear" and "router_chan 4 4 1 0" in r and r.count("router_end") == 12


class FakeProc:
    def __init__(self, argv, **kw):
        import io

        class In(io.StringIO):
            def close(inner):
                self.closed_with = inner.getvalue()
        self.argv = argv
        self.stdin = In()
        self.stdout = io.StringIO("> \nfluidsynth: warning: test\n")
        self.pid = 999999
        self.closed_with = None
        self.rc = None

    def poll(self):
        return self.rc

    def wait(self, timeout=None):
        self.rc = 0
        return 0

    def terminate(self):
        self.rc = -15

    def kill(self):
        self.rc = -9


def test_fluid_process_protocol():
    procs = []
    fs = SM.FluidSynth(popen=lambda argv, **kw: procs.append(FakeProc(argv)) or procs[-1])
    fs.start(["fluidsynth", "x.sf2"], ["x.sf2"])
    assert fs.running
    assert fs.load_font("/home/user/FM-1/SoundFonts/Mein Kit.sf2") == 2
    fs.apply([SM.Channel(5, program=4), SM.Channel(6, enabled=False)], gain=0.5)
    fs.panic()
    sent = procs[0].stdin.getvalue().splitlines()
    assert sent[0] == 'load "/home/user/FM-1/SoundFonts/Mein Kit.sf2" 0'
    assert "gain 0.50" in sent and "select 4 1 0 4" in sent and "select 5 1 0 0" not in sent
    assert "cc 15 123 0" in sent
    fs.stop()
    assert procs[0].closed_with.endswith("quit\n") and not fs.running
    time.sleep(0.05)
    assert any("warning" in x for x in fs.log)
