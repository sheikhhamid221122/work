"""Declarative pre-flight validator.

Mirrors the checks a buyer's AP system runs, so a bad invoice is caught here
instead of in the buyer's rejection e-mail. Findings are advisory: they are
logged and returned, never raised, so invoice generation cannot be blocked by
a rule someone mistyped in a profile.
"""

from __future__ import annotations

import re

from .normalizers import clean_text, digits_only, tokens

VALUE_RESOLVERS = {
    "supplier_name":    lambda d: d.get("sellerBusinessName"),
    "supplier_ntn":     lambda d: d.get("sellerNTNCNIC"),
    "supplier_strn":    lambda d: d.get("sellerSTRN"),
    "supplier_address": lambda d: d.get("sellerAddress"),
    "buyer_name":       lambda d: d.get("buyerBusinessName"),
    "buyer_ntn":        lambda d: d.get("buyerNTNCNIC"),
    "buyer_strn":       lambda d: d.get("buyerSTRN"),
    "buyer_address":    lambda d: d.get("buyerAddress"),
    "po":               lambda d: d.get("PO") or d.get("poNumber"),
    "invoice_date":     lambda d: d.get("invoiceDate"),
    "time_of_issue":    lambda d: d.get("issueTime"),
    "invoice_number":   lambda d: d.get("invoiceRefNo"),
    "fbr_invoice":      lambda d: d.get("fbrInvoiceNumber"),
    "currency":         lambda d: d.get("currency"),
}


def _as_float(value):
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return 0.0


def _evaluate(rule, data):
    field = rule.get("field")
    kind = rule.get("rule")
    param = rule.get("param")
    resolver = VALUE_RESOLVERS.get(field)
    value = clean_text(resolver(data)) if resolver else clean_text(data.get(field))

    if kind == "present":
        return bool(value)
    if kind == "matches":
        return bool(re.search(str(param), value or "", re.I))
    if kind == "digits_len":
        return len(digits_only(value)) == int(param)
    if kind == "min_tokens":
        return len(tokens(value)) >= int(param)
    if kind == "contains_all":
        return tokens(param).issubset(tokens(value))
    if kind == "forbid_tokens":
        return not (tokens(param) & tokens(value))
    if kind == "equals_normalized":
        return digits_only(value) == digits_only(param)
    if kind == "arithmetic":
        return abs(_as_float(data.get("totalExcl")) + _as_float(data.get("totalTax"))
                   - _as_float(data.get("totalInclusive"))) < 0.01
    return True          # unknown rule type: ignore rather than fail closed


def validate(data, rules):
    """Return [{field, severity, message}] for every rule that did not hold."""
    findings = []
    for rule in (rules or []):
        try:
            ok = _evaluate(rule, data)
        except Exception as exc:
            print("[compliance] rule {} errored: {}".format(rule, exc))
            continue
        if not ok:
            findings.append({
                "field": rule.get("field"),
                "severity": rule.get("severity", "error"),
                "message": rule.get("message") or "{} failed check '{}'".format(
                    rule.get("field"), rule.get("rule")),
            })
    return findings


def collect_rules(profile, buyer_override=None):
    """Client-level rules + rules attached to the matched buyer."""
    rules = list((profile or {}).get("rules") or [])
    rules.extend((buyer_override or {}).get("rules") or [])
    return rules
