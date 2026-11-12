# SPDX-License-Identifier: GPL-3.0-only
"""Robust installation through USB hubs: careful mode, automatic resume, flash log.

What goes wrong between the computer and the FM-1 when a hub sits in between (docs/flash-protocol.md, section 18):

* a response from the computer is lost or arrives late: the device asks for the same block again after 5 s
  (Melodee/Felucca ota.c: 3 tries × 5 s per read request, then it gives up). The installer answers every
  request; queued copies of a request that the device has repeated are answered only once, because the
  device holds just ONE received frame at a time (usb.c: frames arriving while one is pending are dropped),
  so a second, stale answer can push out the next real one;
* the hub re-enumerates the FM-1 (short power dip, overloaded hub, autosuspend): the MIDI port disappears and
  comes back, possibly under the same name. The update loader then waits for a new start (loader.c: back in the
  main loop after a failed session, update mode stays until a write succeeds) – the same resume as the
  “Recovery” page, done automatically, only on the pinned device and only with its own loader;
* re-enumeration through hubs takes longer: longer waits for the loader and for the restart.

Nothing here weakens a check: model, loader family (rule), pinning by USB path/serial and the package checksum
are verified exactly as before (Engine.write, fm1_install.own_loader). Careful mode only waits longer, paces the
responses and resumes where the protocol allows it.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from ..i18n import _
from ..vendor import fm1_install as fi

# careful mode (seconds); the tests shorten them (sim.fast_careful_delays)
CAREFUL = {
    "reply": 0.02,          # pause after a request before answering it (normal: DELAY["reply"])
    "gap": 0.03,            # at least this long between two responses (one response in flight at a time)
    "start": 3.0,           # after UPGRADE
    "loader": 5.0,          # after step 1, before looking for the loader
    "reboot": 5.0,          # after writing, before looking for the restarted firmware
    "wait_loader": 120.0,   # the update loader appears (re-enumeration through hubs is slower)
    "wait_reboot": 120.0,   # the FM-1 comes back after writing
    "wait_resume": 90.0,    # automatic resume: the loader comes back after a dropout
    "idle_check": 16.0,     # step 1: the device gives up after 3 × 5 s without an answer
    "loader_giveup": 20.0,  # step 2, Felucca loader: no request for this long = its session has ended
}
RESUME_TRIES = 3            # automatic resumes of step 2
STEP1_TRIES = 2             # repetitions of step 1 (writes nothing) after a USB dropout
MODES = ("auto", "always", "never")


def resolve_mode(setting: str, link) -> bool:
    """careful mode on? setting "auto" (default: when the FM-1 is behind a hub), "always", "never"."""
    setting = (setting or "auto").lower()
    if setting == "always":
        return True
    if setting == "never":
        return False
    return bool(link is not None and getattr(link, "behind_hub", False))


def delays(careful: bool) -> dict:
    d = dict(fi.DELAY)
    d.setdefault("gap", 0.0)
    d.setdefault("wait_resume", d.get("wait_loader", 30.0))
    d.setdefault("loader_giveup", 0.0)
    d.setdefault("resend_min", 2.0)     # a repeat after this long is the device's retry (5 s), not a legitimate re-read
    if careful:
        for k, v in CAREFUL.items():
            d[k] = max(d.get(k, 0.0), v) if k not in ("reply", "gap") else v
    return d


# ------------------------------------------------------------ flash log ---
KERNEL_RE = re.compile(r"\busb\b|xhci|\bhub\b|snd|midi|fm-?1|melodee|felucca|over-?current|voltage|"
                       r"reset (full|high|low)-speed|disconnect", re.I)
SERIAL_RE = re.compile(r"(SerialNumber:\s*)(\S+)", re.I)


def _mask_serial(text: str) -> str:
    return SERIAL_RE.sub(lambda m: m.group(1) + ("…" + m.group(2)[-4:] if len(m.group(2)) > 4 else "…"), text)


class FlightLog:
    """detailed record of one installation: every request (address, length, time, gap), repeated requests
    (= lost responses), ports disappearing/reappearing, resumes, kernel messages of that time.
    Saved next to the journal (DATA_DIR/flash/logs) and added to the error report – without personal paths
    or host names (redact.redact_text). English: it is meant for developers."""

    KEEP = 10

    def __init__(self, folder: Path | None = None, clock: Callable[[], float] = time.monotonic,
                 kernel: Callable[[], tuple] | None = None):
        self.folder = Path(folder) if folder else None
        self.clock = clock
        self.kernel = kernel
        self.t0 = clock()
        self.wall0 = time.time()
        self.lines: list[str] = []
        self.requests = 0
        self.resends = 0
        self.duplicates = 0
        self.max_gap = 0.0
        self.long_gaps = 0              # > 2 s without a request while serving
        self.vanished = 0               # the FM-1's port disappeared
        self.reappeared: list[float] = []   # seconds until it was back
        self.resumes = 0
        self.step1_retries = 0
        self.send_failures = 0
        self.outcome = ""
        self.kernel_lines: list[str] = []
        self.path: Path | None = None
        self._gone_at: float | None = None
        self.phase = ""

    def t(self) -> float:
        return self.clock() - self.t0

    def ev(self, kind: str, text: str = ""):
        self.lines.append(f"{self.t():9.3f}  {kind:<9} {_mask_serial(text)}".rstrip())

    def set_phase(self, phase: str):
        if phase != self.phase:
            self.phase = phase
            self.ev("phase", phase)

    def request(self, addr: int, n: int, gap: float, resend: bool):
        self.requests += 1
        self.max_gap = max(self.max_gap, gap)
        if gap > 2.0:
            self.long_gaps += 1
        if resend:
            self.resends += 1
        self.ev("RESEND" if resend else "req", f"{addr:#010x}+{n} gap {gap:.3f}s")

    def ports(self, appeared: list[str], vanished: list[str]):
        for n in vanished:
            self.ev("port-", n)
        for n in appeared:
            self.ev("port+", n)

    def device_gone(self, what: str = "", expected: bool = False):
        """the device left the bus; expected=True for its own restarts (step 1 -> loader, loader -> firmware)"""
        if self._gone_at is None:
            self._gone_at = self.t()
            if not expected:
                self.vanished += 1
            self.ev("restart" if expected else "gone", what)

    def device_back(self, what: str = ""):
        if self._gone_at is not None:
            dt = self.t() - self._gone_at
            self.reappeared.append(dt)
            self._gone_at = None
            self.ev("back", f"{what} after {dt:.1f}s")

    def summary(self) -> list[str]:
        back = ", ".join(f"{x:.1f}s" for x in self.reappeared) or "-"
        return [f"requests {self.requests}, repeated by the device {self.resends} (lost/late responses), "
                f"duplicates skipped {self.duplicates}, send failures {self.send_failures}",
                f"longest gap {self.max_gap:.1f}s, gaps > 2 s: {self.long_gaps}",
                f"port lost {self.vanished}x, back after: {back}",
                f"step 1 repeated {self.step1_retries}x, step 2 resumed automatically {self.resumes}x"]

    def _collect_kernel(self):
        if self.kernel is None:
            return
        try:
            text, _src, _problem = self.kernel()
        except Exception:  # noqa: BLE001 – diagnostics never break the installation
            return
        if not text:
            return
        from .. import syscheck
        start, end = self.t0 - 10.0, self.clock() + 5.0
        for ln in syscheck.parse_log(text):
            if ln.ts is not None and start <= ln.ts <= end and KERNEL_RE.search(ln.msg):
                self.kernel_lines.append(_mask_serial(ln.raw.strip()))
        self.kernel_lines = self.kernel_lines[-300:]

    @property
    def kernel_hints(self) -> set[str]:
        """what the kernel said about USB during the installation"""
        txt = "\n".join(self.kernel_lines).lower()
        out = set()
        if re.search(r"over-?current", txt):
            out.add("overcurrent")
        if re.search(r"under-?voltage", txt):
            out.add("undervoltage")
        if re.search(r"error -71|error -110|device descriptor read|not accepting address|unable to enumerate", txt):
            out.add("enumeration")
        if "usb disconnect" in txt:
            out.add("disconnect")
        return out

    def finish(self, outcome: str, error: str = "", header: list[str] | None = None) -> Path | None:
        self.outcome = outcome
        self._collect_kernel()
        if self.folder is None:
            return None
        from ..redact import redact_text
        head = [f"FM-1 Companion flash log – {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.wall0))}",
                f"outcome: {outcome}" + (f" – {error}" if error else ""), *(header or []), "", *self.summary(), "",
                "time [s]   event"]
        body = head + self.lines + ["", f"kernel messages during the installation ({len(self.kernel_lines)}):",
                                    *self.kernel_lines]
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            p = self.folder / f"flash-{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.wall0))}.txt"
            k = 2
            while p.exists():
                p = p.with_name(f"{p.stem.split('_')[0]}_{k}.txt")
                k += 1
            p.write_text(redact_text("\n".join(body) + "\n"), "utf-8")
            for old in sorted(self.folder.glob("flash-*.txt"))[:-self.KEEP]:
                old.unlink(missing_ok=True)
            self.path = p
            return p
        except OSError:
            return None


def latest_logs(folder: Path, n: int = 3) -> list[Path]:
    try:
        return sorted(Path(folder).glob("flash-*.txt"))[-n:]
    except OSError:
        return []


# -------------------------------------------------- backend with a watch ---
class WatchedLink:
    """a MIDI link whose `lost` also notices a re-enumeration under the same port name (USB device number changed)"""

    def __init__(self, link, token: Callable[[], str]):
        self._link = link
        self._token = token
        self._first = token()

    def __getattr__(self, k):
        return getattr(self._link, k)

    @property
    def lost(self):
        if self._link.lost:
            return True
        if self._first:
            now = self._token()
            return bool(now) and now != self._first
        return False

    def send(self, pkt):
        return self._link.send(pkt)

    def read(self, timeout):
        return self._link.read(timeout)

    def drain(self):
        return self._link.drain()

    def close(self):
        return self._link.close()


class WatchBackend:
    """wraps the MIDI backend: records ports appearing/disappearing in the flash log, refuses to open a port that
    is not the pinned device (defence in depth – the port filter already only offers that one), and gives every
    link a re-enumeration watch"""

    def __init__(self, backend, details: Callable[[str], dict], flight: FlightLog | None = None,
                 guard: Callable[[str], bool] | None = None):
        self.backend = backend
        self.details = details
        self.flight = flight
        self.guard = guard
        self._last: list[str] | None = None

    def __getattr__(self, k):
        return getattr(self.backend, k)

    def input_names(self):
        names = self.backend.input_names()
        if self.flight is not None and self._last is not None and names != self._last:
            self.flight.ports([n for n in names if n not in self._last], [n for n in self._last if n not in names])
        self._last = list(names)
        return names

    def output_names(self):
        return self.backend.output_names()

    def token(self, name: str) -> str:
        try:
            d = self.details(name) or {}
        except Exception:  # noqa: BLE001
            return ""
        return f"{d.get('usb_path', '')}#{d.get('devnum', '')}" if d.get("devnum") else ""

    def open(self, in_name, out_name):
        if self.guard is not None and not self.guard(in_name):
            if self.flight is not None:
                self.flight.ev("refused", f"not the pinned device: {in_name}")
            raise OSError("not the pinned device")
        link = self.backend.open(in_name, out_name)
        return WatchedLink(link, lambda: self.token(in_name))


# ------------------------------------------------------------ updater ---
class RobustUpdater(fi.Updater):
    """fm1_install.Updater with careful mode, de-duplicated responses, automatic resume and a flash log.
    In normal mode it behaves like the original (same waits), plus the log and the duplicate rule."""

    def __init__(self, backend, port=None, port_filter=None, cancelled=None, *, careful: bool = False,
                 flight: FlightLog | None = None, pinned: bool = False, felucca: bool = False,
                 log: Callable[[str], None] | None = None):
        super().__init__(backend, port, port_filter=port_filter, cancelled=cancelled)
        self.careful = careful
        self.flight = flight or FlightLog()
        self.pinned = pinned            # automatic repetitions only for a pinned device
        self.felucca = felucca          # the package's own loader is a Felucca-family loader (3 × 5 s per read)
        self.say = log or (lambda m: None)
        self.d = delays(careful)
        self._loader = None
        self._product = ""
        self._start = ""
        self._evidence = False          # the last serve() saw signs of a bad connection
        self._last_send = 0.0

    # ------------------------------------------------------------ search
    def wait_for(self, want, secs):
        end = time.monotonic() + secs
        t0 = time.monotonic()
        while time.monotonic() < end:
            dev = self.find(want, only_port=False)
            if dev:
                self.flight.ev("found", f"{dev.id.text} at {dev.name} after {time.monotonic() - t0:.1f}s")
                self.flight.device_back(dev.id.text)
                return dev
            time.sleep(self.d["retry"])
        self.flight.ev("timeout", f"nothing suitable within {secs:.0f}s")
        return None

    # ------------------------------------------------------------ serving
    def _send(self, link, pkt) -> bool:
        if self.d["gap"]:
            wait = self.d["gap"] - (time.monotonic() - self._last_send)
            if wait > 0:
                time.sleep(wait)
        ok = link.send(pkt)
        self._last_send = time.monotonic()
        if not ok:
            self.flight.send_failures += 1
            self.flight.ev("sendfail")
        return ok

    def serve(self, link, image, finish, idle, progress):
        """answer read requests until the device asks for `finish`; -> (served, finished, lost).
        Every request is answered once; copies of a request that are still queued behind it (the device
        repeated it because our answer was late) are skipped – the device keeps only one frame."""
        served, last = 0, time.monotonic()
        prev = None                       # last request answered: (fl, addr, n)
        pending: list = []
        self._evidence = False
        while True:
            if not pending:
                pkt = link.read(self.d["poll"])
                if pkt is None:
                    if link.lost:
                        self.flight.device_gone("port lost while serving")
                        self._evidence = True
                        return served, False, True
                    if time.monotonic() - last > idle:
                        self.flight.ev("idle", f"no request for {time.monotonic() - last:.1f}s")
                        return served, False, False
                    continue
                batch = [pkt]
                while (more := link.read(0)) is not None:      # everything already received, without waiting
                    batch.append(more)
                reqs = [r for r in (fi.parse_request(p) for p in batch) if r]
                for i, r in enumerate(reqs):
                    later = reqs[i + 1:]
                    if r in later:
                        why = "queued twice – answered once"
                    elif r == prev and later:
                        why = "already answered, the device has moved on"
                    else:
                        pending.append(r)
                        continue
                    self.flight.duplicates += 1
                    self.flight.ev("dup", f"{r[1]:#010x}+{r[2]} {why}")
                continue
            fl, addr, n = pending.pop(0)
            now = time.monotonic()
            # the same request again after a pause: the device waited for an answer that did not arrive
            resend = prev == (fl, addr, n) and now - last >= self.d["resend_min"]
            if resend:
                self._evidence = True
            self.flight.request(addr, n, now - last, resend)
            last = now
            if addr in (fi.FINISH_CHECK, fi.FINISH_WRITE):
                self._send(link, fi.response(addr, b"success\0"))
                prev = (fl, addr, n)
                if addr == finish:
                    self.flight.ev("finish", f"{addr:#010x}")
                    return served, True, False
                continue
            if n > fi.MAXDATA or addr + n > len(image):
                raise fi.InstallError("badreq", f"the device asked for {addr:#x}+{n}, outside the package")
            time.sleep(self.d["reply"])
            self.last_read = addr
            if not self._send(link, fi.response(addr, image[addr:addr + n], fl)):
                self.flight.device_gone("send failed")
                self._evidence = True
                return served, False, True
            prev = (fl, addr, n)
            served += 1
            progress(served, addr + n)

    # ------------------------------------------------------------ step 1
    def _check_once(self, dev, image, step, loader):
        if self.cancelled():                                   # last safe cancel point (nothing sent yet)
            dev.link.close()
            raise fi.InstallError("cancelled", "cancelled before anything was sent")
        self.flight.set_phase("step1")
        step("start", dev.id.text)
        self._send(dev.link, fi.UPGRADE)
        time.sleep(self.d["start"])
        try:
            served, finished, lost = self.serve(dev.link, image, fi.FINISH_CHECK, self.d["idle_check"],
                                                lambda k, end: step("check", k))
        finally:
            dev.link.close()
        if not finished:
            raise fi.InstallError("lost" if lost else "stopped",
                                  f"the FM-1 {'was disconnected' if lost else 'stopped answering'} after "
                                  f"{served} requests; nothing was written")
        step("loader")
        self.flight.set_phase("loader")
        time.sleep(self.d["loader"])
        self.flight.device_gone("restarting into the update loader", expected=True)
        ota = self.wait_for(lambda i: i.loader, self.d["wait_loader"])
        if not ota:
            raise fi.InstallError("noloader", "the update loader did not appear. Replug the USB cable and "
                                              "run the install again: the FM-1 stays in update mode until it is done")
        if loader:
            fi.own_loader(ota, loader)
        return ota

    def check(self, dev, image, step, loader):
        """step 1; in careful mode repeated after a USB dropout (step 1 writes nothing, the old firmware keeps
        running) – but not after a plain stop without any sign of a bad connection: that is the device refusing
        the package (FAILED -3/-5/-7 …), and repeating does not help"""
        start = dev.id.text
        tries = 0
        while True:
            try:
                return self._check_once(dev, image, step, loader)
            except fi.InstallError as e:
                if not (self.careful and self.pinned) or e.code not in ("lost", "stopped") or tries >= STEP1_TRIES:
                    raise
                if e.code == "stopped" and not self._evidence:
                    raise
                tries += 1
                self.flight.step1_retries += 1
                self.flight.ev("retry1", f"step 1 again ({tries}/{STEP1_TRIES}) after: {e}")
                self.say(_("USB connection interrupted in step 1 (nothing was written). Trying step 1 again "
                           "({n} of {max}) …").format(n=tries, max=STEP1_TRIES))
                nxt = self.wait_for(lambda i: (not i.loader and i.text == start) or
                                    (i.loader and fi.loader_ok(i.text, loader)), self.d["wait_reboot"])
                if nxt is None:
                    raise e
                if nxt.id.loader:                  # step 1 had finished after all: the own loader waits
                    step("loader")
                    self.flight.set_phase("loader")
                    fi.own_loader(nxt, loader)
                    return nxt
                dev = nxt

    # ------------------------------------------------------------ step 2
    def _write_once(self, ota, image, step, auto: bool):
        if not auto and self.cancelled() and getattr(ota, "resume", False):   # a resume may still be cancelled
            ota.link.close()
            raise fi.InstallError("cancelled", "cancelled before anything was sent")
        self.flight.set_phase("writing")
        step("write", 0)
        self._send(ota.link, fi.UPGRADE)
        time.sleep(self.d["start"])
        idle = self.d["idle_write"]
        if self.careful and self.felucca and self.d["loader_giveup"]:
            idle = min(idle, self.d["loader_giveup"])
        try:
            served, finished, lost = self.serve(ota.link, image, fi.FINISH_WRITE, idle,
                                                lambda k, end: step("write", min(99, end * 100 // len(image))))
        finally:
            ota.link.close()
        if not finished:
            raise fi.InstallError("lost" if lost else "stopped",
                                  f"the loader {'was disconnected' if lost else 'stopped answering'} after "
                                  f"{served} requests. Reconnect and run the install again. On firmware with boot "
                                  "protection, power on holding OCT- alone if update mode does not appear; three "
                                  "short key-light flashes mean update mode")
        step("write", 100)

    def write(self, ota, image, step):
        """step 2; in careful mode resumed automatically (up to RESUME_TRIES) when the loader disappears or stops
        asking: the loader keeps waiting in update mode until a whole write succeeds, and a new start reads the
        package from the beginning – exactly what “Recovery › Finish installation” does. Only the pinned device,
        only its own loader (the package's loader rule), only the same model."""
        tries = 0
        auto = False
        while True:
            try:
                return self._write_once(ota, image, step, auto)
            except fi.InstallError as e:
                if not (self.careful and self.pinned) or e.code not in ("lost", "stopped") or tries >= RESUME_TRIES \
                        or self._loader is None:
                    raise
                tries += 1
                self.flight.resumes += 1
                self.flight.ev("resume", f"attempt {tries}/{RESUME_TRIES} after: {e}")
                self.say(_("The connection to the update loader dropped while writing (typical for USB hubs). Waiting "
                           "for the FM-1 in update mode and starting the write again ({n} of {max}) …").format(
                               n=tries, max=RESUME_TRIES))
                model = fi.model_of(self._product)
                loader, product = self._loader, self._product
                done_ok = product != self._start      # same identity as before: cannot tell written from untouched
                # the own loader again – or already the new firmware: the loader finishes and restarts even when
                # the computer's answer to its last request (0xF0000000) was lost (ldr_core.c: 4 tries, then reset)
                nxt = self.wait_for(lambda i: (i.loader and fi.loader_ok(i.text, loader) and
                                               fi.model_of(i.text) == model) or
                                    (done_ok and not i.loader and i.text == product),
                                    self.d["wait_resume"])
                if nxt is None:
                    raise fi.InstallError(e.code, f"{e} (automatic resume: the loader did not come back)")
                if not nxt.id.loader:
                    nxt.link.close()
                    self.flight.ev("written", f"the FM-1 already runs {nxt.id.text}")
                    step("write", 100)
                    return None
                nxt.resume = True
                ota = nxt
                auto = True

    def verify(self, product, step):
        self.flight.set_phase("reboot")
        step("reboot")
        time.sleep(self.d["reboot"])
        self.flight.device_gone("restarting with the new firmware", expected=True)
        back = self.wait_for(lambda i: not i.loader, self.d["wait_reboot"])
        if not back:
            raise fi.InstallError("noreturn", "the FM-1 did not come back: power-cycle it")
        back.link.close()
        self.last_port = back.name
        if back.id.text != product:
            raise fi.InstallError("mismatch", f"written, but the FM-1 reports {back.id.text} (expected {product})")
        step("done", back.id.text)
        return back.id.text

    def install(self, dev, image, product, step=lambda *a: None, loader=None):
        self._loader, self._product, self._start = loader, product, dev.id.text
        return super().install(dev, image, product, step, loader)


# ------------------------------------------------------------ advice ---
def diagnosis(flight: FlightLog, link, code: str, careful: bool) -> str:
    """plain-language cause and next step after a failed installation ("" when nothing points to the USB link)"""
    hub = link is not None and getattr(link, "behind_hub", False)
    sleepy = link is not None and bool(getattr(link, "autosuspend_nodes", []))
    k = flight.kernel_hints
    usb_trouble = code in ("lost", "stopped", "noloader", "noreturn") and (
        flight.resends or flight.vanished or flight.send_failures or flight.long_gaps or hub or k)
    if not usb_trouble:
        return ""
    parts = []
    if "overcurrent" in k or "undervoltage" in k:
        parts.append(_("The kernel reported a power problem (over-current or under-voltage) during the installation."))
    if hub:
        parts.append(_("The FM-1 is connected through a USB hub ({path}). Hubs are the most common reason why an "
                       "installation starts but does not finish.").format(path=link.path))
    if flight.resends:
        parts.append(_("{n} answers from the computer did not reach the FM-1 in time (it had to ask again).").format(
            n=flight.resends))
    if flight.vanished:
        parts.append(_("The FM-1 dropped off the USB bus {n}× during the installation.").format(n=flight.vanished))
    if sleepy:
        parts.append(_("USB power saving (autosuspend) is switched on for the FM-1 or its hub."))
    steps = [_("Connect the FM-1 directly to the computer – on a Raspberry Pi preferably to a black USB 2 port – "
               "with a short data cable.") if hub else
             _("Use a short USB data cable and a port directly on the computer – on a Raspberry Pi preferably a "
               "black USB 2 port."),
             _("If you have to use a hub: one with its own power supply, and no other power-hungry devices on it.")]
    if sleepy:
        steps.append(_("Install the optional udev rule that keeps the FM-1 and its hubs awake (see Help › “Switching "
                       "firmware”, section “USB hubs”)."))
    if not careful:
        steps.append(_("Switch on Settings › “Careful USB mode for flashing: always”."))
    return " ".join(parts) + " " + _("Next step: {steps}").format(steps=" ".join(steps)) + " " + \
        _("The flash log is saved and included in the bug report (Help › Create bug report).")


def plan_notes(link, setting: str = "auto") -> list[str]:
    """notes for the installation plan: hub, careful mode, autosuspend"""
    out = []
    if link is None or not getattr(link, "found", False):
        return out
    careful = resolve_mode(setting, link)
    if link.behind_hub:
        hubs = "; ".join(h.describe() for h in link.hubs)
        out.append(_("<b>Connected through a USB hub – careful mode.</b> The FM-1 ({path}) is behind {n} hub(s): {hubs}. "
                     "The app waits longer, answers more slowly and resumes the write automatically if the "
                     "connection drops. Safer still: connect the FM-1 directly to the computer.").format(
                         path=link.path, n=link.depth, hubs=hubs) if careful else
                   _("The FM-1 ({path}) is connected through a USB hub, careful mode is switched off in the "
                     "settings. Hubs are the most common reason why an installation does not finish.").format(
                         path=link.path))
        if link.bus_powered_hubs:
            out.append(_("The hub in front of the FM-1 says it is bus-powered (no own power supply). Use a hub with "
                         "its own power supply or connect the FM-1 directly."))
    elif careful:
        out.append(_("Careful USB mode is switched on (settings)."))
    if link.autosuspend_nodes:
        out.append(_("USB power saving (autosuspend) is on for: {devices}. It can put the FM-1 or its hub to sleep "
                     "during the waits. The optional udev rule of the app switches it off (Help › “Switching "
                     "firmware”, section “USB hubs”).").format(devices=", ".join(n.name for n in link.autosuspend_nodes)))
    return out


def udev_rule_path() -> Path | None:
    """the optional udev rule shipped with the app (source tree, user installation, Debian package)"""
    here = Path(__file__).resolve().parents[2]
    for p in (here / "packaging" / "udev" / "60-fm1-companion.rules", here / "udev" / "60-fm1-companion.rules",
              Path("/usr/share/fm1-companion/udev/60-fm1-companion.rules")):
        if p.exists():
            return p
    return None


UDEV_INSTALLED = Path("/etc/udev/rules.d/60-fm1-companion.rules")


def udev_hint() -> str:
    src = udev_rule_path()
    if UDEV_INSTALLED.exists():
        return ""
    s = str(src) if src else "packaging/udev/60-fm1-companion.rules"
    return (f"sudo install -m 0644 '{s}' /etc/udev/rules.d/ && sudo udevadm control --reload-rules && "
            "sudo udevadm trigger --subsystem-match=usb --action=change")
