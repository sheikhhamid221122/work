"""The catalogue of fields that can appear in the buyer / invoice info block.

Adding a new printable field = one entry here. Choosing which fields a given
client prints = a profile JSON edit, never a code change.
"""

from __future__ import annotations

import re

from .normalizers import clean_text, digits_only

# key -> (default label, resolver(data) -> value)
FIELD_CATALOG = {
    "buyer_ntn":      ("BUYER NTN/CNIC", lambda d: clean_text(d.get("buyerNTNCNIC"))),
    "buyer_strn":     ("BUYER STRN",     lambda d: clean_text(d.get("buyerSTRN"))),
    "buyer_province": ("BUYER PROVINCE", lambda d: clean_text(d.get("buyerProvince"))),
    "status":         ("STATUS",         lambda d: "Registered" if str(
                                             d.get("buyerRegistrationType", "")).strip().lower()
                                             in ("registered", "r", "yes", "1") else "Unregistered"),
    "fbr_invoice":    ("FBR INVOICE #",  lambda d: clean_text(d.get("fbrInvoiceNumber"))),
    "po":             ("P.O #",          lambda d: clean_text(d.get("PO") or d.get("poNumber"))),
    "purchase_order": ("PURCHASE ORDER #", lambda d: clean_text(d.get("PO") or d.get("poNumber"))),
    "dn":             ("D.N. No.",       lambda d: clean_text(d.get("DN"))),
    "dc":             ("DC #",           lambda d: clean_text(d.get("CNIC"))),
    "cnic":           ("CNIC",           lambda d: clean_text(d.get("CNIC"))),
    "hs_code":        ("HS CODE",        lambda d: clean_text(
                                             (d.get("items") or [{}])[0].get("hs_code")
                                             or (d.get("items") or [{}])[0].get("hsCode"))),
    "currency":       ("CURRENCY",       lambda d: clean_text(d.get("currency") or "PKR")),
    "time_of_issue":  ("TIME OF ISSUE",  lambda d: clean_text(d.get("issueTime"))),
    "sale_type":      ("SALE TYPE",      lambda d: clean_text(d.get("saleType"))),
    "delivery_date":  ("DELIVERY DATE",  lambda d: clean_text(d.get("deliveryDate"))),
}

# Some invoice fields have no dedicated input on the create-invoice form and
# are typed as free-form custom fields ("PO#", "Sales Tax No."). When the main
# payload key is empty, a row falls back to a custom field whose name matches
# one of these aliases, and that custom field is then not repeated at the
# bottom of the block. Matching ignores case, spaces and punctuation.
CUSTOM_FIELD_ALIASES = {
    "po": ["PO", "PO#", "P.O", "P.O #", "PO NO", "PO NUMBER",
           "PURCHASE ORDER", "PURCHASE ORDER #"],
    "purchase_order": ["PO", "PO#", "P.O", "P.O #", "PO NUMBER",
                       "PURCHASE ORDER", "PURCHASE ORDER #"],
    "buyer_strn": ["STRN", "SALES TAX NO", "SALES TAX NUMBER",
                   "SALES TAX REGISTRATION NO", "BUYER STRN"],
    "buyer_ntn": ["NTN", "NTN/CNIC", "BUYER NTN", "BUYER NTN/CNIC"],
    "dn": ["DN", "D.N", "D.N. NO", "DELIVERY NOTE"],
    "dc": ["DC", "DC #", "DELIVERY CHALLAN"],
    "time_of_issue": ["TIME", "TIME OF ISSUE"],
    "delivery_date": ["DELIVERY DATE"],
    "sale_type": ["SALE TYPE"],
}


def _key(value):
    """Normalised comparison key: 'P.O #' and 'po' collapse to 'PO'."""
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


# Fallback ordering used when a profile does not list `rows`. Mirrors the
# legacy tpl_* checkbox behaviour exactly.
LEGACY_SETTING_FOR = {
    "buyer_strn": "show_buyer_strn",
    "status": "show_status",
    "fbr_invoice": "show_fbr_invoice_buyer",
    "hs_code": "show_hs_code_buyer",
    "cnic": "show_cnic",
    "dn": "show_dn",
    "dc": "show_dc",
    "po": "show_po",
    "purchase_order": "show_purchase_order",
}

DEFAULT_ROW_ORDER = [
    "buyer_ntn", "status", "buyer_strn", "fbr_invoice", "hs_code",
    "cnic", "dn", "dc", "po", "purchase_order",
]


def build_info_rows(data, settings=None, spec=None, custom_fields_max=None):
    """Resolve the info block into [{key, label, value}, ...].

    `spec` is the merged profile section for this invoice (client-level rows /
    labels, overlaid with any buyer-level override).
    """
    settings = settings or {}
    spec = spec or {}
    labels = spec.get("labels") or {}
    required = list(spec.get("require") or [])
    wanted = list(spec.get("rows") or [])

    if not wanted:
        wanted = ["buyer_ntn"]
        for key in DEFAULT_ROW_ORDER[1:]:
            flag = LEGACY_SETTING_FOR.get(key)
            if flag and settings.get(flag):
                wanted.append(key)

    for key in required:                      # a required field always prints
        if key in FIELD_CATALOG and key not in wanted:
            wanted.append(key)

    custom = list(data.get("customFields") or [])
    consumed = set()

    def from_custom_fields(key):
        """Pull a value from a matching custom field and mark it consumed."""
        wanted_keys = {_key(a) for a in CUSTOM_FIELD_ALIASES.get(key, [])}
        if not wanted_keys:
            return ""
        for index, field in enumerate(custom):
            if index in consumed:
                continue
            if _key(field.get("name")) in wanted_keys:
                value = clean_text(field.get("value"))
                if value:
                    consumed.add(index)
                    return value
        return ""

    rows = []
    for key in wanted:
        entry = FIELD_CATALOG.get(key)
        if not entry:
            continue
        default_label, resolver = entry
        try:
            value = resolver(data)
        except Exception:
            value = ""
        if not value:
            value = from_custom_fields(key)
        if not value and key not in required:
            continue
        rows.append({"key": key, "label": labels.get(key, default_label), "value": value})

    # Per-invoice free-form custom fields still work, appended at the end —
    # minus any that a row above already consumed or duplicates, so the same
    # value never prints twice (which also keeps the two-column pairing even).
    printed_labels = {_key(r["label"]) for r in rows}
    printed_values = {_key(r["value"]) for r in rows if r["value"]}
    shown = 0
    for index, field in enumerate(custom):
        if index in consumed:
            continue
        if custom_fields_max is not None and shown >= custom_fields_max:
            break
        name, value = clean_text(field.get("name")), clean_text(field.get("value"))
        if not (name and value):
            continue
        if _key(name) in printed_labels or _key(value) in printed_values:
            continue
        rows.append({"key": "custom", "label": name, "value": value})
        shown += 1

    return rows


def match_key(*values):
    """Normalised lookup key for buyer overrides (tax ids compare digit-only)."""
    for value in values:
        key = digits_only(value)
        if key:
            yield key
