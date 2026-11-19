# SPDX-License-Identifier: GPL-3.0-only
"""Remove secrets and personal details from everything the app hands out
(bug report, diagnostics, system check report, copied reports).

* Keys that look like credentials (token, passw…, secret, api_key/api-key) are replaced by ``REDACTED`` at any
  nesting depth – no matter what they contain.
* Known token formats (GitHub ``ghp_…``/``github_pat_…``, ``Authorization: …``) are also replaced in free
  text (logs, error messages); so are user name and password inside addresses (``://user:pass@host``) and the
  query part of web addresses (``?session=…``).
* The home directory becomes ``~``, the host name ``<rechner>``, the user name ``<benutzer>`` (any
  capitalisation); the folder name after ``/home/``, ``/media/``, ``/run/media/`` is replaced as well.
* Hardware addresses (MAC, also Bluetooth) become ``<mac>``, Wi-Fi names after ``SSID`` ``<netz>``, and numbers
  that follow the word "serial" keep only their last four characters. So does every unit ID – the flash chip's
  16-hex-digit unique ID (M-Flasher's "unit id", m-bootloader's uid, also written with 0x) – wherever it appears in
  text; SHA-256 values and other longer hex runs stay readable.
* Keys that name a device's identity (``serial…``, ``unit_id``/``unit id``/``unit_ids``, ``unique_id``, ``uid``,
  ``chip key``/``chip_key``) keep only the last four characters of their value, at any nesting depth.
* ``extra`` (literal text -> placeholder) lets a caller remove texts only it knows, e.g. device names.

This is pattern matching, not understanding: file names, project names and free text a user typed can still
contain personal details. Texts shown to users must say so.
"""
from __future__ import annotations

import getpass
import platform
import re
from pathlib import Path

# placeholders in shared reports (meant for developers) – not translated
REDACTED = "(entfernt)"  # i18n: ignore
HOST_PLACEHOLDER = "<rechner>"  # i18n: ignore
USER_PLACEHOLDER = "<benutzer>"  # i18n: ignore
MAC_PLACEHOLDER = "<mac>"  # i18n: ignore
NET_PLACEHOLDER = "<netz>"  # i18n: ignore
PATH_PLACEHOLDER = "<path>"  # i18n: ignore
SECRET_KEY = re.compile(r"token|passw|secret|api[_-]?key", re.IGNORECASE)
_TEXT_PATTERNS = [
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+|token\s+)?[^\s\"',;]+"),
    re.compile(r"(?i)((?:token|passw(?:or)?d?|secret|api[_-]?key)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"),
]
# user[:password]@ in an address – the whole part before the @ goes
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]{1,15}://)[^\s/@\"'<>]+@")
# query part of a web address (session keys, share links …); the address itself stays
_URL_QUERY = re.compile(r"(?i)(\bhttps?://[^\s?#\"'<>]+)\?[^\s\"'<>]*")
_MAC = re.compile(r"(?<![0-9A-Fa-f:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])")
_SSID = re.compile(r"(?i)(\bE?SSID\b\s*[=:]?\s*)(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;)\"']+)")
# "SerialNumber: 3F2A9C0011223344", "serial 20240117000123", "serial=ABCDEF123456" -> last four characters stay
_SERIAL = re.compile(r"(?i)(serial(?:[\s_-]*(?:number|nummer|no\.?|nr\.?))?[\"']?\s*[:=#]?\s*[\"']?)"
                     r"(?=[0-9A-Za-z]*\d)[0-9A-Za-z]{4,}([0-9A-Za-z]{4})\b")
_USER_DIRS = re.compile(r"((?:/home|/media|/run/media|/Users)/)(?!<)[^/\s\"'<>:]+")


def is_secret_key(key) -> bool:
    return isinstance(key, str) and bool(SECRET_KEY.search(key))


_ID_KEY = re.compile(r"serial|unit[\s_-]?ids?$|unique[\s_-]?ids?$|^uids?$|chip[\s_-]?key", re.IGNORECASE)
# a unit id in text: 16 hex digits, also with 0x, not inside a longer hex run (SHA-256 values stay readable). The
# spaced/colon byte forms are masked where the app writes them (flight logs: robust._mask_serial, tools/runner); in
# reports they are left alone, so that SysEx hex dumps in the app log stay readable.
_UNIT_ID = re.compile(r"(?<![0-9A-Za-z])(?:0[xX])?[0-9A-Fa-f]{16}(?![0-9A-Za-z])")


def is_serial_key(key) -> bool:
    """a key whose value identifies one unit: serial numbers, unit ids, the chip's unique id, the chip key"""
    return isinstance(key, str) and bool(_ID_KEY.search(key.strip()))


def mask_unit_ids(text: str) -> str:
    """every unit id in a text -> "…" + its last four hex digits"""
    return _UNIT_ID.sub(lambda m: "…" + re.sub(r"[^0-9A-Fa-f]", "", m.group(0))[-4:], text)


def mask_serial(value):
    """keep the last four characters of a serial number (enough to tell two devices apart)."""
    if isinstance(value, str) and len(value) > 4:
        return "…" + value[-4:]
    return value


def strip_url_secrets(text: str) -> str:
    """remove ``user:pass@`` and the query part from web addresses in a text."""
    out = _URL_USERINFO.sub(lambda m: m.group(1) + REDACTED + "@", text)
    return _URL_QUERY.sub(lambda m: m.group(1) + "?" + REDACTED, out)


def _identity() -> tuple[str, str, str]:
    try:
        home = str(Path.home())
    except (RuntimeError, KeyError):
        home = ""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = ""
    return home, platform.node() or "", user


def redact_text(text: str, home: str | None = None, host: str | None = None, user: str | None = None,
                extra: dict | None = None) -> str:
    if not isinstance(text, str) or not text:
        return text
    h, n, u = _identity()
    home = h if home is None else home
    host = n if host is None else host
    user = u if user is None else user
    out = text
    for i, pat in enumerate(_TEXT_PATTERNS):
        out = pat.sub(REDACTED if i == 0 else (lambda m: m.group(1) + REDACTED), out)
    out = strip_url_secrets(out)
    for literal, placeholder in sorted((extra or {}).items(), key=lambda kv: -len(kv[0])):
        if literal:
            out = out.replace(literal, placeholder)
    if home and home not in ("/", "/root"):
        out = out.replace(home, "~")
    out = _USER_DIRS.sub(lambda m: m.group(1) + USER_PLACEHOLDER, out)
    if host and len(host) >= 2 and host.lower() not in ("localhost",):
        out = re.sub(rf"(?i)(?<![\w.-]){re.escape(host)}(?![\w-])", HOST_PLACEHOLDER, out)
    if user and len(user) >= 3 and user.lower() not in ("root", "user", "pi"):
        # also the possessive form ("Michaels FM-1"): the name followed by a single s
        out = re.sub(rf"(?i)(?<![\w.-]){re.escape(user)}(?=s?(?![\w-]))", USER_PLACEHOLDER, out)
    out = _MAC.sub(MAC_PLACEHOLDER, out)
    out = _SSID.sub(lambda m: m.group(1) + NET_PLACEHOLDER, out)
    out = _SERIAL.sub(lambda m: m.group(1) + "…" + m.group(2), out)
    return mask_unit_ids(out)


def redact(obj, home: str | None = None, host: str | None = None, user: str | None = None,
           extra: dict | None = None):
    """recursively cleaned copy of dict/list/str (other values unchanged)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if is_secret_key(k):
                out[k] = REDACTED if v not in ("", None, [], {}) else v
                continue
            out[k] = redact(v, home, host, user, extra)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(x, home, host, user, extra) for x in obj]
    if isinstance(obj, str):
        return redact_text(obj, home, host, user, extra)
    return obj


__all__ = ["REDACTED", "HOST_PLACEHOLDER", "USER_PLACEHOLDER", "MAC_PLACEHOLDER", "NET_PLACEHOLDER",
           "PATH_PLACEHOLDER", "is_secret_key", "is_serial_key", "mask_serial", "mask_unit_ids", "strip_url_secrets", "redact",
           "redact_text"]
