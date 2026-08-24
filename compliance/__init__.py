"""
Per-client invoice compliance layer
===================================

Some buyers (e.g. Point Print PK) run an automated Purchase Invoice Checker
that rejects a supplier invoice unless specific fields, labels and identifier
formats appear on the printed PDF. This package makes those requirements
*configuration* instead of per-customer `if` statements.

How it plugs in
---------------
    from compliance import (
        get_profile, merge_template_settings, apply_compliance,
        run_validation, resolve_template_name,
    )

    profile = get_profile(username)            # None for every other client
    if profile:
        settings = merge_template_settings(settings, profile)
        data = apply_compliance(data, settings, profile)
        run_validation(data, profile)          # logs advisory findings

Safety contract
---------------
* No profile -> `get_profile` returns None -> nothing runs -> other clients
  render exactly as they did before this package existed.
* `apply_compliance` never raises; on any internal error it returns the
  payload unchanged so invoice generation continues.
* Profiles apply to sandbox and production alike (both use the same routes).

Adding another client: drop a JSON file in `compliance/profiles/`.
See `compliance/README.md` for the full field reference.
"""

from __future__ import annotations

from .fields import FIELD_CATALOG, build_info_rows
from .normalizers import clean_text, compose_address, digits_only, format_date, local_now
from .registry import (
    all_profiles,
    get_buyer_override,
    get_profile,
    merge_template_settings,
    resolve_template_name,
)
from .rules import collect_rules, validate

__all__ = [
    "FIELD_CATALOG", "all_profiles", "apply_compliance", "build_info_rows",
    "clean_text", "compose_address", "digits_only", "format_date",
    "get_buyer_override", "get_profile", "merge_template_settings",
    "resolve_template_name", "run_validation", "validate",
]

# Fields whose printed form may be digit-normalised, if a profile asks for it.
_NORMALIZABLE = {
    "seller_ntn": "sellerNTNCNIC",
    "seller_strn": "sellerSTRN",
    "buyer_ntn": "buyerNTNCNIC",
    "buyer_strn": "buyerSTRN",
}


def apply_compliance(data, settings=None, profile=None, custom_fields_max=None):
    """Enrich an invoice payload just before rendering.

    Only ever called for a client that has a profile; a no-op otherwise.
    Returns the same dict (mutated in place) for call-site convenience.
    """
    if not profile or not isinstance(data, dict):
        return data

    try:
        settings = settings or {}
        buyer = get_buyer_override(profile, data)
        seller_cfg = profile.get("seller") or {}

        # --- seller block -------------------------------------------------
        if seller_cfg.get("legal_name") and not data.get("sellerLegalName"):
            data["sellerLegalName"] = seller_cfg["legal_name"]
        if seller_cfg.get("address"):
            data["sellerAddress"] = seller_cfg["address"]
        elif seller_cfg.get("address_suffix"):
            data["sellerAddress"] = compose_address(
                data.get("sellerAddress"), *seller_cfg["address_suffix"])

        # --- identifier formatting (opt-in per profile) -------------------
        for key in (profile.get("normalize") or []):
            payload_key = _NORMALIZABLE.get(key)
            if payload_key and data.get(payload_key):
                data[payload_key] = digits_only(data[payload_key])

        # --- buyer canonical values (exact strings the buyer's AP expects) -
        if buyer.get("canonical_name"):
            data["buyerBusinessName"] = clean_text(buyer["canonical_name"])
        if buyer.get("canonical_address"):
            data["buyerAddress"] = clean_text(buyer["canonical_address"])
        if buyer.get("canonical_ntn"):
            data["buyerNTNCNIC"] = clean_text(buyer["canonical_ntn"])
        if buyer.get("canonical_strn"):
            data["buyerSTRN"] = clean_text(buyer["canonical_strn"])

        # --- statutory extras --------------------------------------------
        data.setdefault("currency", profile.get("currency", "PKR"))
        if not data.get("issueTime"):
            time_fmt = profile.get("time_format", "%I:%M %p")
            data["issueTime"] = local_now(profile.get("timezone")).strftime(time_fmt)

        date_fmt = (profile.get("template") or {}).get("date_format")
        if date_fmt:
            data["invoiceDateDisplay"] = format_date(data.get("invoiceDate"), date_fmt)

        # --- the resolved info block --------------------------------------
        spec = {
            "rows": buyer.get("rows") or profile.get("rows"),
            "labels": dict(profile.get("labels") or {}, **(buyer.get("labels") or {})),
            "require": list(profile.get("require") or []) + list(buyer.get("require") or []),
        }
        data["infoRows"] = build_info_rows(data, settings, spec, custom_fields_max)
    except Exception as exc:                      # never break invoicing
        print("[compliance] apply_compliance skipped: {}".format(exc))
    return data


def run_validation(data, profile=None, log=True):
    """Validate against the profile's rules. Advisory only."""
    if not profile:
        return []
    findings = validate(data, collect_rules(profile, get_buyer_override(profile, data)))
    if log:
        for finding in findings:
            print("[compliance] {severity}: {field} -> {message}".format(**finding))
    return findings
