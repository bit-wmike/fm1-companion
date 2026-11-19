#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stand-in for M-Flasher 0.12.1's command line (read-only commands), for the tests.

The output follows the line formats of M-Flasher's src/cli.rs and src/ops.rs at tag v0.12.1 (see
fm1companion/tools/mflasher_parse.py) with SYNTHETIC values only – no unit id, path or hash of a real device.
The usage text is M-Flasher's own (MIT, (c) 2026 Julius Vitkauskas), so that the --help contract can be tested.

State: the JSON file named by FAKE_MFLASHER_STATE (all keys optional):
  mode      "download" (one unit in ROM download mode, default) | "midi" (running on USB MIDI) | "none" |
            "two" (two units in download mode) | "sg-missing"
  unit_id   default "0123456789abcdef" ("" prints "–")
  flash     default 0x856014;  chip_key default 0x980F;  bootloader default "stock (M-VAVE)"
  fault     "" | "crash" (killed by a signal) | "garbage" (info without its block) | "hang" (sleeps) |
            "refuse" (exit 1 with "Refused: …")
  version   what --version prints (default "0.12.1")
Every call is appended to the state's "calls" list (when the file is writable), so tests see the arguments.
"""
import json
import os
import signal
import sys
import time

USAGE = """usage: m-flasher [find | download-mode | restart | info | backup [DIR] | restore BACKUP.bin [--keep-bootloader] |
                 firmware PACKAGE.fwsc |
                 bootloader [--stock PACKAGE.fwsc | --file UBOOT.boot] |
                 run-ram IMAGE --addr ADDR [--call OPCODE]... [--read ADDR:LEN -o OUT]]
       options: --device PATH  --board fm1|smk37  --backup-dir DIR  --yes
                --restart (restore, firmware, bootloader: start the unit when written)
       without arguments: the window (`m-flasher run-ram --help`: the run-ram command)"""
SG_DRIVER = ("a unit is in download mode, but Linux's SCSI generic driver, which M-Flasher reaches it through, is "
             "not loaded: run `sudo modprobe sg`, then try again")


def state() -> dict:
    p = os.environ.get("FAKE_MFLASHER_STATE")
    if not p:
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def record(st: dict, args):
    p = os.environ.get("FAKE_MFLASHER_STATE")
    if not p:
        return
    st.setdefault("calls", []).append(args)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except OSError:
        pass


def refuse(text: str) -> int:
    print(f"Refused: {text}", file=sys.stderr)
    return 1


def main(args) -> int:
    st = state()
    record(st, args)
    mode, fault = st.get("mode", "download"), st.get("fault", "")
    if "--version" in args:
        print(st.get("version", "0.12.1"))
        return 0
    if "-h" in args or "--help" in args:
        print(USAGE)
        return 0
    if fault == "crash":
        os.kill(os.getpid(), signal.SIGSEGV)
    if fault == "hang":
        time.sleep(60)
    if fault == "refuse":
        return refuse("the unit did not answer")
    cmd = args[0] if args else ""
    sgs = {"download": ["/dev/sg1"], "two": ["/dev/sg1", "/dev/sg2"]}.get(mode, [])
    if cmd == "find":
        for p in sgs:
            print(f"{p}\tWL82 UBOOT1.00 ({p})")
        if mode == "midi":
            print("midi\tFM-1 (running; download-mode switches it)")
        if mode == "sg-missing":
            print(SG_DRIVER, file=sys.stderr)
        return 0
    if cmd == "info":
        dev = args[args.index("--device") + 1] if "--device" in args else ""
        if mode == "sg-missing":
            return refuse(SG_DRIVER)
        if not sgs:
            return refuse("no unit in download mode found: put it in download mode first")
        if not dev:
            if len(sgs) > 1:
                return refuse("more than one unit in download mode: choose one with --device")
            dev = sgs[0]
        if dev not in sgs:
            print(f"{dev}: No such file or directory (os error 2)", file=sys.stderr)
            return 1
        flash = int(st.get("flash", 0x856014))
        print(f"Connecting to {dev}")
        print("Starting the M-Flasher loader 1.3")
        print(f"Flash chip 0x{flash:06X} ({'old' if flash == 0x856014 else 'new'})")
        print("Reading the flash")
        for n in (25, 50, 75, 100):
            sys.stderr.write(f"\r  {n:3d}%")
            sys.stderr.flush()
        sys.stderr.write("\n")
        print("Read in 1.2 s (830 KB/s)")
        print("Checked: the chip's CRC32 of the flash matches the read")
        if fault == "garbage":
            print("  something else entirely")
            return 0
        uid = st.get("unit_id", "0123456789abcdef") or "–"
        rows = [("model", "FM-1"), ("bootloader", st.get("bootloader", "stock (M-VAVE)")),
                ("firmware", st.get("firmware", "Felucca")), ("app", "OK"),
                ("chip key", f"0x{int(st.get('chip_key', 0x980F)):04X}"), ("flash", f"0x{flash:06X}"),
                ("unit id", uid), ("loader", "M-Flasher 1.3, CPU at 96 MHz")]
        for k, v in rows:
            print(f"  {k:11} {v}")
        print("    - the firmware uses the FM-1 flash layout")
        print(f"    - flash chip 0x{flash:06X} ({'old' if flash == 0x856014 else 'new'})")
        return 0
    return refuse(USAGE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
