#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stand-in for M-Flasher 0.12.1's command line, for the tests.

The output follows the line formats of M-Flasher's src/cli.rs and src/ops.rs at tag v0.12.1 (see
fm1companion/tools/mflasher_parse.py) with SYNTHETIC values only – no unit id, path or hash of a real device; the
"flash" is a pseudo-random 1 MiB image made from a fixed seed. The usage text is M-Flasher's own (MIT, (c) 2026
Julius Vitkauskas), so that the --help contract can be tested.

Commands: find, info, backup, restore, bootloader --file X.boot / --stock P.fwsc, firmware P.fwsc (each writing one
with the "Type "yes" to write: " prompt on stdin), download-mode, restart. The bootloader and firmware plans follow
ops.rs plan_bootloader / plan_firmware: their titles, their one note, the boot area [0, 0x4000) only (bootloader) or
[0x4000, end of the package's flash.bin) (firmware).

State: the JSON file named by FAKE_MFLASHER_STATE (all keys optional):
  mode      "download" (one unit in ROM download mode, default) | "midi" (running on USB MIDI) | "none" |
            "two" (two units in download mode) | "two-midi" (two running units) | "sg-missing"
  unit_id   default "0123456789abcdef" ("" prints "–")
  session_unit_id  the unit id that backup/restore sessions print (default: unit_id) – a unit swapped after `info`
  flash     default 0x856014;  chip_key default 0x980F;  bootloader default "stock (M-VAVE)"
  image     the unit's flash (a 1 MiB file; default: "<state file>.flash", made on first use from a fixed seed)
  fault     for every command: "" | "crash" (killed by a signal) | "garbage" (info without its block) |
            "hang" (sleeps) | "refuse" (exit 1 with "Refused: …")
  fault_<command>   only for that command (e.g. fault_restore), wins over fault:
            backup:  "read-mismatch" (every run reads one byte differently) | "crc-mismatch" (the chip's CRC differs,
                     read again, no "Checked" line) | "crc-refuse" ("two reads of the flash differ") | "slow" (2 s)
            restore: "other-unit" (session shows another unit id) | "other-chip" | "plan-extra" (one more range in
                     the plan) | "warning" (a WARNING line) | "slow" (3 s before the prompt) | "hang" |
                     "write-fail" (second write does not read back) | "killed-in-write" (SIGKILL during the write) |
                     "no-done" (writes, but no "Done" line) | "other-unit-plan" (as M-Flasher plans another unit's
                     backup: factory range left out, with its warning) | "drift" (one byte in sector 0 changed
                     since the app's backup: same plan, but M-Flasher's own backup differs) | "slow-write" (0.7 s per
                     run) | "backup-fails" (its own backup before the first erase fails: exit 1, nothing erased)
            the same-unit plan carries M-Flasher's factory WARNING whenever the image's 0xFF000-0xFFFFF differs
            bootloader / firmware: "other-unit" | "other-chip" | "plan-outside" (one more range 0x04000-0x04FFF in the
                     plan of a bootloader, 0x03000-0x03FFF in a firmware's) | "plan-extra" (a second plan line) |
                     "warning" (M-Flasher's flash-chip WARNING) | "note" (another note) | "killed-in-write" |
                     "write-fail" | "no-done" | "backup-fails" | "refuse-key" (the package does not match the chip
                     key: Refused before the plan) | "wrong-area" (writes a slightly different boot area than it
                     announced – the app's fresh read must catch it) | "stray" (one unknown line before the title)
  midi_name the ALSA card name `find` prints for a running unit (default "FM-1")
            download-mode: "never" (the unit does not come back within 20 s)
            info:    "split-utf8" (no unit id; the "–" is written in two pieces)
  version   what --version prints (default "0.12.1")
Every call is appended to the state's "calls" list; what came on stdin at a prompt goes to "answers"; every sector
range written goes to "written" (the tests prove with these that nothing was written).
"""
import hashlib
import json
import os
import random
import re
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
FLASH_SIZE, SECTOR, FACTORY = 0x100000, 0x1000, (0xFF000, 0x100000)
PROMPT = 'Type "yes" to write: '


def state_path():
    return os.environ.get("FAKE_MFLASHER_STATE") or ""


def state() -> dict:
    p = state_path()
    if not p:
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(st: dict):
    p = state_path()
    if not p:
        return
    try:
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(p + ".tmp", p)
    except OSError:
        pass


def record(st: dict, args):
    st.setdefault("calls", []).append(args)
    save(st)


def refuse(text: str) -> int:
    print(f"Refused: {text}", file=sys.stderr)
    return 1


def image_path(st: dict) -> str:
    return st.get("image") or ((state_path() or "/nonexistent/fake") + ".flash")


def synthetic_image(seed: str = "fm1c-fake-flash") -> bytes:
    """1 MiB of pseudo-random bytes from a fixed seed, with a little structure at the start"""
    b = bytearray(random.Random(seed).randbytes(FLASH_SIZE))
    b[0:16] = b"FAKE-FM1-FLASH\x00\x01"
    return bytes(b)


def load_image(st: dict) -> bytearray:
    p = image_path(st)
    try:
        with open(p, "rb") as f:
            return bytearray(f.read())
    except OSError:
        img = synthetic_image()
        try:
            with open(p, "wb") as f:
                f.write(img)
        except OSError:
            pass
        return bytearray(img)


def store_image(st: dict, img: bytes):
    with open(image_path(st), "r+b" if os.path.exists(image_path(st)) else "wb") as f:
        f.write(img)
        f.flush()
        os.fsync(f.fileno())


def fault_of(st: dict, cmd: str) -> str:
    return st.get(f"fault_{cmd}", "") or st.get("fault", "")


def opt(args, name):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else ""


def ranges_text(runs):
    return ", ".join(f"0x{a:05X}-0x{b - 1:05X}" for a, b in runs)


def runs_of(sectors):
    out = []
    for a in sectors:
        if out and out[-1][1] == a:
            out[-1][1] = a + SECTOR
        else:
            out.append([a, a + SECTOR])
    return [tuple(r) for r in out]


def session(st: dict, cmd: str, dev: str, fault: str):
    """Connecting … the info block: (image as read, unit id shown) or an exit code"""
    sgs = {"download": ["/dev/sg1"], "two": ["/dev/sg1", "/dev/sg2"]}.get(st.get("mode", "download"), [])
    if st.get("mode") == "sg-missing":
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
    if cmd in ("restore", "bootloader", "firmware") and fault == "other-chip":
        flash = 0xB36014 if flash == 0x856014 else 0x856014
    img = load_image(st)
    if cmd == "restore" and fault == "drift":         # the flash changed since the app's backup, inside sector 0
        img[0x10] ^= 0xFF
    print(f"Connecting to {dev}")
    print("Starting the M-Flasher loader 1.3")
    print(f"Flash chip 0x{flash:06X} ({'old' if flash == 0x856014 else 'new'})")
    print("Reading the flash")
    for n in (25, 50, 75, 100):
        sys.stderr.write(f"\r  {n:3d}%")
        sys.stderr.flush()
    sys.stderr.write("\n")
    print("Read in 1.2 s (830 KB/s)")
    if cmd == "backup" and fault == "read-mismatch":
        n = st.get("reads", 0) + 1
        st["reads"] = n
        save(st)
        img[0x50000] ^= n & 0xFF
    if cmd == "backup" and fault == "crc-refuse":
        print("The chip's CRC32 does not match the read: reading it again")
        return refuse("two reads of the flash differ: the USB connection is unreliable. Nothing was written. "
                      "Try another cable or port, without a hub.")
    if cmd == "backup" and fault == "crc-mismatch":
        print("The chip's CRC32 does not match the read: reading it again")
    else:
        print("Checked: the chip's CRC32 of the flash matches the read")
    if fault == "garbage":
        print("  something else entirely")
        return 0
    uid = st.get("unit_id", "0123456789abcdef")
    if fault == "split-utf8":
        uid = ""
    if cmd in ("backup", "restore", "bootloader", "firmware") and "session_unit_id" in st:
        uid = st["session_unit_id"]
    if cmd in ("restore", "bootloader", "firmware") and fault == "other-unit":
        uid = "fedcba9876543210"
    rows = [("model", "FM-1"), ("bootloader", st.get("bootloader", "stock (M-VAVE)")),
            ("firmware", st.get("firmware", "Felucca")), ("app", "OK"),
            ("chip key", f"0x{int(st.get('chip_key', 0x980F)):04X}"), ("flash", f"0x{flash:06X}"),
            ("unit id", uid or "–"), ("loader", "M-Flasher 1.3, CPU at 96 MHz")]
    for k, v in rows:
        if k == "unit id" and fault == "split-utf8":       # "–" arrives in two reads (L8)
            sys.stdout.write(f"  {k:11} ")
            sys.stdout.flush()
            b = "–\n".encode()
            sys.stdout.buffer.write(b[:1])
            sys.stdout.buffer.flush()
            time.sleep(0.3)
            sys.stdout.buffer.write(b[1:])
            sys.stdout.buffer.flush()
            continue
        print(f"  {k:11} {v}")
    print("    - the firmware uses the FM-1 flash layout")
    print(f"    - flash chip 0x{flash:06X} ({'old' if flash == 0x856014 else 'new'})")
    return bytes(img), uid, rows


def save_backup(st: dict, folder: str, img: bytes, uid: str, rows) -> str:
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    i = 1
    while True:
        p = os.path.join(folder, f"fm1-{stamp}.bin" if i == 1 else f"fm1-{stamp}-{i}.bin")
        if not os.path.exists(p) and not os.path.exists(p[:-4] + ".json"):
            break
        i += 1
    with open(p, "xb") as f:
        f.write(img)
    body = "{\n" + "".join(f'  "{k}": "{v}",\n' for k, v in rows)
    body += f'  "sha256": "{hashlib.sha256(img).hexdigest()}",\n  "saved": "{stamp}",\n  "tool": "m-flasher 0.12.1"\n}}\n'
    with open(p[:-4] + ".json", "x", encoding="utf-8") as f:
        f.write(body)
    return p


def json_text(text: str, key: str):
    m = re.search('"' + re.escape(key) + r'"\s*:\s*"([^"]*)"', text)
    return m.group(1) if m else None


def restore(st: dict, args, fault: str) -> int:
    pos = [a for i, a in enumerate(args[1:], 1) if not a.startswith("--") and not args[i - 1].startswith("--")]
    if not pos:
        return refuse(USAGE)
    f = pos[0]
    try:
        with open(f, "rb") as fh:
            data = fh.read()
    except OSError as e:
        print(f"cannot read {f}: {e.strerror}", file=sys.stderr)
        return 1
    side = None
    try:
        with open(os.path.splitext(f)[0] + ".json", encoding="utf-8") as fh:
            side = fh.read()
    except FileNotFoundError:
        pass
    if side is not None and '"sha256"' in side:
        if (json_text(side, "sha256") or "").lower() != hashlib.sha256(data).hexdigest():
            return refuse(f"{f} is not the file that was saved: its SHA-256 differs from the one in its .json.")
    r = session(st, "restore", opt(args, "--device"), fault)
    if isinstance(r, int):
        return r
    current, uid, rows = r
    if len(data) != FLASH_SIZE:
        return refuse(f"not a full backup: {len(data)} bytes, {FLASH_SIZE} expected")
    if fault == "slow":
        time.sleep(3)
    if fault == "hang":
        time.sleep(60)
    backup_uid = json_text(side or "", "unit id")
    same = (backup_uid == uid) if backup_uid and uid else data[FACTORY[0]:] == current[FACTORY[0]:]
    if fault == "other-unit-plan":
        same = False
    allowed = [(0, FLASH_SIZE)] if same else [(0, FACTORY[0])]
    sectors = [a for a in range(0, FLASH_SIZE, SECTOR) if data[a:a + SECTOR] != current[a:a + SECTOR]
               and any(s <= a < e for s, e in allowed)]
    runs = runs_of(sectors)
    shown = list(runs)
    if fault == "plan-extra":
        shown.append((0xF0000, 0xF1000))
    print()
    print("Restore a backup")
    if not sectors:
        print("Nothing to write: the flash already holds this.")
    else:
        n = len(sectors) + (1 if fault == "plan-extra" else 0)
        print(f"Writes {n} sectors ({n * 4} KiB): {ranges_text(shown)}")
    if same:
        print("The whole flash is restored, the bootloader included.")
        if data[FACTORY[0]:] != current[FACTORY[0]:]:          # (ops.rs: any difference, "blank" or not)
            print("WARNING: This unit's factory data at 0xFF000-0xFFFFF is blank; the backup's is restored. "
                  "Use only a backup of this unit.")
    else:
        print("WARNING: This backup seems to come from another unit: its factory data (0xFF000-0xFFFFF: "
              "calibration, Bluetooth address) is not restored, this unit keeps its own.")
    if fault == "warning":
        print("WARNING: This unit's factory data at 0xFF000-0xFFFFF is blank; the backup's is restored. "
              "Use only a backup of this unit.")
    if not sectors:
        return 0
    sys.stdout.write(PROMPT)
    sys.stdout.flush()
    answer = sys.stdin.readline()
    st = state()
    st.setdefault("answers", []).append(answer)
    save(st)
    if answer.strip() != "yes":
        print("Nothing written.")
        return 0
    return apply(st, args, current, data, runs, fault, uid, rows)


def apply(st, args, current: bytes, target: bytes, runs, fault: str, uid: str, rows, after=None) -> int:
    """after "yes": M-Flasher's own backup, then each run erased and written ("Writing …"), then "Done" – with the
    faults of the write phase. after(st): what the unit reports afterwards (e.g. its bootloader)"""
    folder = opt(args, "--backup-dir") or os.path.expanduser("~/Documents/M-Flasher/backups")
    if fault == "backup-fails":
        print(f"cannot write {folder}/fm1-x.bin: No space left on device (os error 28)", file=sys.stderr)
        return 1
    print(f"Backup saved: {save_backup(st, folder, current, uid, rows)}")
    img = bytearray(current)
    for i, (a, b) in enumerate(runs):
        print(f"Writing 0x{a:05X}-0x{b - 1:05X}")
        sys.stdout.flush()
        st = state()
        st.setdefault("written", []).append([a, b])
        save(st)
        if fault == "slow-write":
            time.sleep(0.7)
        if fault == "killed-in-write":
            img[a:a + SECTOR] = b"\xff" * SECTOR            # erased, not yet programmed
            store_image(st, bytes(img))
            os.kill(os.getpid(), signal.SIGKILL)
        img[a:b] = target[a:b]
        if fault == "write-fail" and i == 0:
            img[a] ^= 0xFF
            store_image(st, bytes(img))
            print(f"Writing 0x{a:05X}-0x{b - 1:05X} (again)")
            return refuse(f"0x{a:05X}-0x{b - 1:05X} does not read back as written. Do not switch the unit off: try "
                          "again, with another cable or port. Your backup: (see above)")
        sys.stderr.write("\r  100%\n")
    if fault == "wrong-area":
        img[0x100] ^= 0x01
    store_image(st, bytes(img))
    if after is not None:
        st = state()
        after(st)
        save(st)
    if fault != "no-done":
        print("Done: everything written is verified (the chip's CRC32 matches)")
    return 0


# ---- packages and bootloader containers (formats.rs: Package::parse, bankcb_check, bootloader_identity)

def crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _i in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def enc(data: bytes, key: int = 0xFFFF) -> bytes:
    out = bytearray(data)
    for i in range(len(out)):
        out[i] ^= key & 0xFF
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xFFFF
    return bytes(out)


def parse_package(raw: bytes):
    """(identity, flash.bin) or a refusal text"""
    if len(raw) < 0x1000:
        return "not a firmware package (.fwsc)"
    n, ident = 20, ""
    for i in range(n):
        m = raw[i * 48 + 47]
        if m == 0x7D:
            break
        ident += chr((m - i - 1) & 0xFF)
    image = b"".join(raw[i * 48:i * 48 + 47] for i in range(n)) + raw[n * 48:]
    head = enc(image[:0x40])
    if crc16(head[2:]) != int.from_bytes(head[0:2], "little"):
        return "not a firmware package, or a damaged one (header CRC)"
    count = int.from_bytes(head[8:10], "little")
    lst = image[0x40:0x40 + 0x50 * count]
    if crc16(lst) != int.from_bytes(head[2:4], "little"):
        return "damaged firmware package (file list CRC)"
    for i in range(count):
        e = enc(lst[i * 0x50:(i + 1) * 0x50])
        typ, off, size = int.from_bytes(e[0:2], "little"), int.from_bytes(e[8:12], "little"), \
            int.from_bytes(e[12:16], "little")
        name = e[0x40:].split(b"\0")[0].decode("latin-1")
        if name == "flash.bin" and typ == 0:
            flash = image[off:off + size]
            if len(flash) <= 0x4000 or len(flash) % SECTOR:
                return f"unexpected flash image size {len(flash)}"
            return ident, flash
    return "the package has no flash image (flash.bin)"


def boot_identity(boot: bytes):
    if len(boot) < 16:
        return None
    bank, size = int.from_bytes(boot[0:2], "little"), int.from_bytes(boot[2:4], "little")
    off, pcrc, hcrc = int.from_bytes(boot[8:12], "little"), int.from_bytes(boot[12:14], "little"), \
        int.from_bytes(boot[14:16], "little")
    if bank != 1 or crc16(boot[:14]) != hcrc or off + size > len(boot) or crc16(boot[off:off + size]) != pcrc:
        return False
    p = boot[off:off + size]
    if len(p) < 0x70 or p[0x40:0x48] != b"AC79BLID":
        return None
    return p[0x48:0x50].split(b"\0")[0].decode(), p[0x50:0x70].split(b"\0")[0].decode()


def show_plan(title: str, runs, notes, warnings, fault: str, extra=None):
    shown = list(runs) + ([extra] if fault == "plan-outside" and extra else [])
    n = sum((b - a) // SECTOR for a, b in shown)
    print()
    if fault == "stray":
        print("Something M-Flasher never says")
    print(title)
    if not shown:
        print("Nothing to write: the flash already holds this.")
    else:
        print(f"Writes {n} sectors ({n * 4} KiB): {ranges_text(shown)}")
        if fault == "plan-extra":
            print("Writes 1 sectors (4 KiB): 0xF0000-0xF0FFF")
    for x in notes + (["This note is new."] if fault == "note" else []):
        print(x)
    for w in warnings + (["This unit's flash chip (0x123456) is not one m-bootloader is known to run on. Install it "
                          "at your own risk."] if fault == "warning" else []):
        print(f"WARNING: {w}")


def ask(st) -> bool:
    sys.stdout.write(PROMPT)
    sys.stdout.flush()
    answer = sys.stdin.readline()
    st = state()
    st.setdefault("answers", []).append(answer)
    save(st)
    if answer.strip() != "yes":
        print("Nothing written.")
        return False
    return True


def bootloader(st: dict, args, fault: str) -> int:
    stock, file = opt(args, "--stock"), opt(args, "--file")
    if not stock and not file:
        return refuse("the app never installs M-Flasher's own bundled bootloader (fake)")
    try:
        with open(stock or file, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        print(f"cannot read {stock or file}: {e.strerror}", file=sys.stderr)
        return 1
    r = session(st, "bootloader", opt(args, "--device"), fault)
    if isinstance(r, int):
        return r
    current, uid, rows = r
    if stock:
        pkg = parse_package(raw)
        if isinstance(pkg, str):
            return refuse(pkg)
        ident, flash = pkg
        if ident.rsplit("_", 1)[0] not in ("FM-1", "MiniFM"):
            return refuse(f"this package is for the {ident.rsplit('_', 1)[0]}, the connected unit is a FM-1")
        if fault == "refuse-key":
            return refuse("the package does not match this unit's chip key (0x980F): its boot area would not start "
                          "the unit's firmware")
        area, title = flash[:0x4000], f"Put back the stock bootloader (from {ident})"
        note = "Only the boot area (0x00000-0x03FFF) is written, all of it from the package."
        shown_after = "stock (M-VAVE)"
    else:
        ident = boot_identity(raw)
        if ident is False:
            return refuse("not a valid bootloader container (BANKCB)")
        if ident is not None and ident[0] != "fm1":
            return refuse(f'this bootloader is built for "{ident[0]}", the unit is a FM-1')
        version = ident[1] if ident else "unknown"
        stored = enc(raw[:16]) + enc(raw[16:])
        area = bytearray(b"\xff" * 0x4000)
        area[:0xA0] = current[:0xA0]
        area[0xA0:0xA0 + len(stored)] = stored
        area[0x3FA0:] = current[0x3FA0:0x4000]
        title = f"Install the bootloader {version} for the FM-1"
        major, _d, minor = version.split("-")[0].partition(".")
        short = major.isdigit() and minor.isdigit() and (int(major), int(minor)) >= (1, 4)
        note = ("Only the boot area (0x00000-0x03FFF) is written. This bootloader does not read the unit's "
                "configuration, so of that only the chip key is kept, with the settings older versions need."
                if short else
                "Only the boot area (0x00000-0x03FFF) is written; the unit's own configuration in it is kept.")
        shown_after = f"m-bootloader {version} (fm1)" if ident else "unknown"
    target = bytes(area) + current[0x4000:]
    sectors = [a for a in range(0, 0x4000, SECTOR) if target[a:a + SECTOR] != current[a:a + SECTOR]]
    runs = runs_of(sectors)
    show_plan(title, runs, [note], [], fault, extra=(0x4000, 0x5000))
    if not runs:
        return 0
    if not ask(st):
        return 0
    return apply(state(), args, current, target, runs, fault, uid, rows,
                 after=lambda s: s.update(bootloader=shown_after))


def firmware(st: dict, args, fault: str) -> int:
    pos = [a for i, a in enumerate(args[1:], 1) if not a.startswith("--") and not args[i - 1].startswith("--")]
    if not pos:
        return refuse(USAGE)
    try:
        with open(pos[0], "rb") as fh:
            raw = fh.read()
    except OSError as e:
        print(f"cannot read {pos[0]}: {e.strerror}", file=sys.stderr)
        return 1
    r = session(st, "firmware", opt(args, "--device"), fault)
    if isinstance(r, int):
        return r
    current, uid, rows = r
    pkg = parse_package(raw)
    if isinstance(pkg, str):
        return refuse(pkg)
    ident, flash = pkg
    if ident.rsplit("_", 1)[0] not in ("FM-1", "MiniFM"):
        return refuse(f"this package is for the {ident.rsplit('_', 1)[0]}, the connected unit is a FM-1")
    if len(flash) > FLASH_SIZE:
        return refuse("the package is larger than the flash")
    if fault == "refuse-key":
        return refuse("the package's firmware does not decrypt with this unit's chip key (0x980F): writing it would "
                      "not start")
    target = bytearray(current)
    target[0x4000:len(flash)] = flash[0x4000:]
    sectors = [a for a in range(0, FLASH_SIZE, SECTOR) if target[a:a + SECTOR] != current[a:a + SECTOR]]
    runs = runs_of(sectors)
    show_plan(f"Install firmware {ident}", runs, [f"Installs {ident} (0x04000-0x{len(flash) - 1:05X})."], [],
              fault, extra=(0x3000, 0x4000))
    if not runs:
        return 0
    if not ask(st):
        return 0
    return apply(state(), args, current, bytes(target), runs, fault, uid, rows,
                 after=lambda s: s.update(firmware=ident))


def main(args) -> int:
    st = state()
    record(st, args)
    mode = st.get("mode", "download")
    cmd = args[0] if args else ""
    fault = fault_of(st, cmd)
    if "--version" in args:
        print(st.get("version", "0.12.1"))
        return 0
    if "-h" in args or "--help" in args:
        print(USAGE)
        return 0
    if "--yes" in args:                              # (the app never passes it: a test catches it here)
        st.setdefault("yes_flag", True)
        save(st)
    if fault == "crash":
        os.kill(os.getpid(), signal.SIGSEGV)
    if fault == "hang" and cmd not in ("restore", "bootloader", "firmware"):
        time.sleep(60)
    if fault == "refuse":
        return refuse("the unit did not answer")
    sgs = {"download": ["/dev/sg1"], "two": ["/dev/sg1", "/dev/sg2"]}.get(mode, [])
    if cmd == "find":
        for p in sgs:
            print(f"{p}\tWL82 UBOOT1.00 ({p})")
        if mode == "midi":
            print(f"midi\t{st.get('midi_name', 'FM-1')} (running; download-mode switches it)")
        if mode == "two-midi":
            print("midi\tFM-1 (running; download-mode switches it)")
            print("midi\tFM-1 (running; download-mode switches it)")
        if mode == "sg-missing":
            print(SG_DRIVER, file=sys.stderr)
        return 0
    if cmd == "download-mode":
        if sgs:
            print("A unit is already in download mode")
            return 0
        if mode == "two-midi":
            return refuse("more than one unit on USB MIDI: connect only one")
        if mode != "midi":
            return refuse("no FM-1 or SMK-37 found on USB MIDI: is it switched on and connected?")
        print("Sent the FM-1 the download-mode message over USB MIDI; waiting for it")
        sys.stdout.flush()
        if fault == "never":
            time.sleep(0.3)
            return refuse("the FM-1 did not come back in download mode within 20 s: its firmware may not know the "
                          "soft key; use its keys instead (see the window's HOW TO CONNECT)")
        st = state()
        st["mode"] = "download"
        save(st)
        print("In download mode: /dev/sg1\tWL82 UBOOT1.00 (/dev/sg1)")
        return 0
    if cmd == "restart":
        if not sgs:
            if mode == "midi":
                print("The FM-1 is running already (on USB MIDI)")
                return 0
            return refuse("no unit in download mode found: put it in download mode first")
        print(f"Connecting to {opt(args, '--device') or sgs[0]}")
        print("Sent the restart (a 34-byte program at 0x01C28000); waiting for the unit to reset")
        st = state()
        st["mode"] = "midi"
        save(st)
        print("Running: the FM-1 is on USB MIDI")
        return 0
    if cmd == "info":
        r = session(st, cmd, opt(args, "--device"), fault)
        return r if isinstance(r, int) else 0
    if cmd == "backup":
        if fault == "slow":
            time.sleep(2)
        r = session(st, cmd, opt(args, "--device"), fault)
        if isinstance(r, int):
            return r
        img, uid, rows = r
        pos = [a for i, a in enumerate(args[1:], 1) if not a.startswith("--") and not args[i - 1].startswith("--")]
        folder = (pos[0] if pos else "") or opt(args, "--backup-dir") or os.path.expanduser("~/Documents/M-Flasher")
        print(f"Backup saved: {save_backup(st, folder, img, uid, rows)}")
        return 0
    if cmd == "restore":
        return restore(st, args, fault)
    if cmd == "bootloader":
        return bootloader(st, args, fault)
    if cmd == "firmware":
        return firmware(st, args, fault)
    return refuse(USAGE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
