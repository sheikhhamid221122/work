"""Small pure helpers shared by the compliance package.

Nothing in here touches Flask, the database or global state, so it is safe to
import from scripts and tests.
"""

from __future__ import annotations

import re
from datetime import datetime

_NON_DIGIT = re.compile(r"\D+")
_WS = re.compile(r"\s+")


def digits_only(value) -> str:
    """0300844204937 out of '03-00-8442-049-37'.

    Used for *comparison* everywhere, and for *display* only where a profile
    explicitly asks for it (see `normalize` in a profile).
    """
    if value is None:
        return ""
    return _NON_DIGIT.sub("", str(value))


def clean_text(value) -> str:
    if value is None:
        return ""
    return _WS.sub(" ", str(value)).strip()


def tokens(value) -> set:
    return {t for t in re.split(r"[^A-Za-z0-9]+", str(value or "").upper()) if t}


def compose_address(*parts) -> str:
    """Join address fragments, dropping blanks and case-insensitive repeats."""
    seen, out = set(), []
    for part in parts:
        part = clean_text(part).strip(",")
        if not part:
            continue
        key = part.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return ", ".join(out)


def format_date(value, fmt):
    """Re-format an ISO invoice date. Returns the input untouched on any
    surprise, so a malformed date can never break invoice generation."""
    if not value or not fmt:
        return value
    for parse_fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(value)[:10], parse_fmt).strftime(fmt)
        except ValueError:
            continue
    return value


def local_now(timezone_name=None):
    """Current time in the client's timezone (falls back to server time)."""
    if timezone_name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(timezone_name))
        except Exception:
            pass
    return datetime.now()
