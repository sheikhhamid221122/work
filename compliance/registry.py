"""Profile registry.

A *compliance profile* describes how one client's invoices must be printed for
buyers that run automated AP checks. Profiles live in `compliance/profiles/`
as JSON, one file per client, keyed by the client's `users.username`.

Design rules that keep this safe to deploy:

  * A client with NO profile is untouched — `get_profile()` returns None and
    every hook in app.py becomes a no-op. Existing clients render byte-for-byte
    as before.
  * Profiles are data. Onboarding another demanding buyer is a new JSON file
    (or a new entry in `buyers`), reviewed in a pull request, no code change.
  * Profiles are environment-agnostic: the same profile applies to sandbox and
    production invoices, because both flow through the same PDF routes.
"""

from __future__ import annotations

import json
import os
import threading

from .fields import match_key
from .normalizers import clean_text

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")

_lock = threading.Lock()
_cache = None


def _load_all():
    profiles = {}
    if not os.path.isdir(PROFILE_DIR):
        return profiles
    for name in sorted(os.listdir(PROFILE_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(PROFILE_DIR, name)
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:                      # never break invoicing
            print("[compliance] skipping malformed profile {}: {}".format(name, exc))
            continue
        if not doc.get("enabled", True):
            continue
        doc.setdefault("_source", name)
        for username in doc.get("usernames", []):
            profiles[clean_text(username).lower()] = doc
    return profiles


def all_profiles(refresh=False):
    global _cache
    with _lock:
        if _cache is None or refresh:
            _cache = _load_all()
        return _cache


def get_profile(username, refresh=False):
    """Return the compliance profile for a client, or None."""
    if not username:
        return None
    return all_profiles(refresh).get(clean_text(username).lower())


def get_buyer_override(profile, data):
    """Find the buyer-specific section of a profile by matching the invoice's
    buyer STRN / NTN (digit-normalised), falling back to an exact name match."""
    if not profile:
        return {}
    buyers = profile.get("buyers") or {}
    if not buyers:
        return {}

    index = {}
    for key, override in buyers.items():
        for candidate in match_key(key, *(override.get("match") or [])):
            index[candidate] = override
        for name in (override.get("match_names") or []):
            index["name:" + clean_text(name).upper()] = override

    for candidate in match_key(data.get("buyerSTRN"), data.get("buyerNTNCNIC")):
        if candidate in index:
            return index[candidate]

    name_key = "name:" + clean_text(data.get("buyerBusinessName")).upper()
    return index.get(name_key, {})


def merge_template_settings(settings, profile):
    """Overlay a profile's `template` block on the client's tpl_* settings.

    Returns a NEW dict; the caller's settings object is never mutated, so an
    unrelated client sharing the same request path is unaffected.
    """
    merged = dict(settings or {})
    if profile:
        merged.update(profile.get("template") or {})
    return merged


def resolve_template_name(username, default_template):
    """Let a profile pin the HTML template (used by the legacy Excel route).
    Clients without a profile keep whatever the caller already decided."""
    profile = get_profile(username)
    if profile and profile.get("template_name"):
        return profile["template_name"]
    return default_template
