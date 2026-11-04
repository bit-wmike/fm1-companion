# SPDX-License-Identifier: GPL-3.0-only
"""Remove secrets and personal details from everything the app hands out
(bug report, diagnostics, system check report, copied reports).

* Keys that look like credentials (token, passw…, secret, api_key/api-key) are replaced by ``REDACTED`` at any
  nesting depth – no matter what they contain.
* Known token formats (GitHub ``ghp_…``/``github_pat_…``, ``Authorization: …``) are also replaced in free
  text (logs, error messages).
* The home directory becomes ``~``, the host name ``<rechner>``, the user name ``<benutzer>``.
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
SECRET_KEY = re.compile(r"token|passw|secret|api[_-]?key", re.IGNORECASE)
_TEXT_PATTERNS = [
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+|token\s+)?[^\s\"',;]+"),
    re.compile(r"(?i)((?:token|passw(?:or)?d?|secret|api[_-]?key)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"),
]


def is_secret_key(key) -> bool:
    return isinstance(key, str) and bool(SECRET_KEY.search(key))


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


def redact_text(text: str, home: str | None = None, host: str | None = None, user: str | None = None) -> str:
    if not isinstance(text, str) or not text:
        return text
    h, n, u = _identity()
    home = h if home is None else home
    host = n if host is None else host
    user = u if user is None else user
    out = text
    for i, pat in enumerate(_TEXT_PATTERNS):
        out = pat.sub(REDACTED if i == 0 else (lambda m: m.group(1) + REDACTED), out)
    if home and home not in ("/", "/root"):
        out = out.replace(home, "~")
    if host and len(host) >= 2 and host.lower() not in ("localhost",):
        out = re.sub(rf"(?<![\w.-]){re.escape(host)}(?![\w-])", HOST_PLACEHOLDER, out)
    if user and len(user) >= 3 and user not in ("root", "user", "pi"):
        out = re.sub(rf"(?<![\w.-]){re.escape(user)}(?![\w-])", USER_PLACEHOLDER, out)
    return out


def redact(obj, home: str | None = None, host: str | None = None, user: str | None = None):
    """recursively cleaned copy of dict/list/str (other values unchanged)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if is_secret_key(k):
                out[k] = REDACTED if v not in ("", None, [], {}) else v
                continue
            out[k] = redact(v, home, host, user)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(x, home, host, user) for x in obj]
    if isinstance(obj, str):
        return redact_text(obj, home, host, user)
    return obj


__all__ = ["REDACTED", "HOST_PLACEHOLDER", "is_secret_key", "redact", "redact_text"]
