"""Shared rules for buyers and products: codes, duplicate checks, validation.

Used by the buyer/product endpoints the invoice form has always called
(invoice_form_routes.py) and by the Buyers / Products pages and their Excel
import (master_data_routes.py), so every way of adding a record follows the
same rules:

- Codes. Every buyer gets B-0001, B-0002, ... and every product P-0001, ...
  numbered per client, continuing after the highest code already in use. The
  number is taken under a per-client transaction lock, so two saves at the
  same moment cannot both get the same code.
- Duplicates. A buyer is a duplicate if another buyer of this client has the
  same NTN/CNIC *or* the same name (either identifies the business). A
  product is a duplicate if another active product has the same name *and*
  the same rate -- the same item at a different price is allowed.

This module imports nothing from the route modules, so both can import it.
"""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

BUYER_CODE_PREFIX = "B"
PRODUCT_CODE_PREFIX = "P"

# pg_advisory_xact_lock(namespace, hashtext(client_id)): one namespace per code series.
_LOCK_NAMESPACE = {"buyers": 7101, "products": 7102}

REGISTRATION_TYPES = ("Registered", "Unregistered")


# ---------------------------------------------------------------- normalising

def normalize_tax_id(value):
    """Same normalisation as invoice_form_routes._normalize_tax_id: a CNIC is
    its 13 digits, anything else keeps only its letters and digits."""
    if value is None:
        return ""
    raw = str(value).strip().upper()
    digits_only = "".join(ch for ch in raw if ch.isdigit())
    if len(digits_only) == 13:
        return digits_only
    return "".join(ch for ch in raw if ch.isalnum())


def valid_tax_id(value):
    """Return the normalised NTN (7 characters) / CNIC (13 digits), or raise."""
    normalized = normalize_tax_id(value)
    if normalized.isdigit() and len(normalized) == 13:
        return normalized
    if len(normalized) == 7 and normalized.isalnum():
        return normalized
    raise ValueError("NTN/CNIC must be 7 characters (NTN) or 13 digits (CNIC)")


def name_key(value):
    """Case- and spacing-insensitive form of a name, for duplicate checks."""
    return " ".join(str(value or "").split()).casefold()


def to_decimal(value, default=None):
    """Parse a money/percent value ("1,450.00", "18%", 18) into a Decimal."""
    if value is None:
        return default
    text = str(value).strip().replace(",", "").replace("%", "").strip()
    if not text:
        return default
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return default
    if not number.is_finite():
        return default
    return number


def rate_key(value):
    """Rate to 2 decimal places, the precision it is stored and shown at."""
    number = to_decimal(value, Decimal("0"))
    return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def canonical_registration_type(value):
    key = str(value or "").strip().lower()
    for option in REGISTRATION_TYPES:
        if key == option.lower():
            return option
    return ""


# ---------------------------------------------------------------------- codes

def format_code(prefix, number):
    return f"{prefix}-{number:04d}"


def _highest_code(cur, table, column, prefix, client_id):
    cur.execute(
        f"""
        SELECT COALESCE(MAX(CAST(SUBSTRING(UPPER(BTRIM({column})) FROM %s) AS BIGINT)), 0)
        FROM {table}
        WHERE client_id = %s
        """,
        # At most 9 digits, so a stray hand-typed code can't overflow the cast.
        (f"^{prefix}-([0-9]{{1,9}})$", client_id),
    )
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def reserve_codes(cur, table, column, prefix, client_id, count=1):
    """Take the next `count` codes for this client, in order.

    Locks the client's code series until the surrounding transaction ends, so
    call this in the same transaction as the INSERTs that use the codes.
    """
    # clients.id is a UUID on the live database, and the lock takes an int4,
    # so the key is a hash of the id. A collision only makes two clients wait
    # for each other briefly; it can never mix their codes.
    cur.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                (_LOCK_NAMESPACE[table], str(client_id)))
    start = _highest_code(cur, table, column, prefix, client_id)
    return [format_code(prefix, start + i) for i in range(1, count + 1)]


def peek_next_code(cur, table, column, prefix, client_id):
    """The code the next record would get (informational -- not reserved)."""
    return format_code(prefix, _highest_code(cur, table, column, prefix, client_id) + 1)


def product_code_column_exists(cur):
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'products' AND column_name = 'product_code'
        """
    )
    return cur.fetchone() is not None


# ----------------------------------------------------------------- duplicates

def load_buyer_keys(cur, client_id):
    """[(id, business_name, ntn_cnic, buyer_code)] for duplicate checks."""
    cur.execute(
        "SELECT id, business_name, ntn_cnic, buyer_code FROM buyers WHERE client_id = %s",
        (client_id,),
    )
    return cur.fetchall()


def find_duplicate_buyer(existing, name, ntn, exclude_id=None):
    """Return (field, row) for the first buyer clashing on NTN or name."""
    want_ntn = normalize_tax_id(ntn)
    want_name = name_key(name)
    for row in existing:
        if exclude_id is not None and row[0] == exclude_id:
            continue
        if want_ntn and normalize_tax_id(row[2]) == want_ntn:
            return "ntn", row
    for row in existing:
        if exclude_id is not None and row[0] == exclude_id:
            continue
        if want_name and name_key(row[1]) == want_name:
            return "name", row
    return None


def buyer_duplicate_message(clash):
    field, row = clash
    who = row[1] or "another buyer"
    code = f" ({row[3]})" if row[3] else ""
    if field == "ntn":
        return f"A buyer with this NTN/CNIC already exists: {who}{code}."
    return f"A buyer named “{who}” already exists{code}."


def find_buyer_code_owner(existing, code, exclude_id=None):
    want = str(code or "").strip().upper()
    if not want:
        return None
    for row in existing:
        if exclude_id is not None and row[0] == exclude_id:
            continue
        if str(row[3] or "").strip().upper() == want:
            return row
    return None


def load_product_keys(cur, client_id):
    """[(id, description, rate, is_active)] for duplicate checks."""
    cur.execute(
        "SELECT id, description, rate, is_active FROM products WHERE client_id = %s",
        (client_id,),
    )
    return cur.fetchall()


def find_duplicate_product(existing, description, rate, exclude_id=None, active=True):
    """First product with the same name and rate.

    active=True looks at live products (a clash); active=False at soft-deleted
    ones (which are restored rather than duplicated).
    """
    want_name = name_key(description)
    want_rate = rate_key(rate)
    for row in existing:
        if exclude_id is not None and row[0] == exclude_id:
            continue
        is_active = row[3] is not False
        if is_active != active:
            continue
        if name_key(row[1]) == want_name and rate_key(row[2]) == want_rate:
            return row
    return None


def product_duplicate_message(row):
    return (f"“{row[1]}” at a rate of {rate_key(row[2]):,.2f} already exists. "
            "Change the name or the rate to add it as a separate product.")


# ----------------------------------------------------------------- validation

HS_CODE_RE = re.compile(r"^\d{4}(\.\d{1,4})?$")


def clean_buyer(data, province_lookup):
    """Validate a buyer payload. Returns (clean_dict, None) or (None, error)."""
    name = " ".join(str(data.get("business_name") or "").split())
    if not name:
        return None, "Business name is required."
    try:
        ntn = valid_tax_id(data.get("ntn_cnic"))
    except ValueError as exc:
        return None, str(exc)
    province = province_lookup(data.get("province"))
    if not province:
        return None, "Province is required."
    reg = canonical_registration_type(data.get("registration_type"))
    if not reg:
        return None, "Registration type must be Registered or Unregistered."
    address = " ".join(str(data.get("address") or "").split())
    if not address:
        return None, "Address is required."
    return {
        "business_name": name[:255],
        "ntn_cnic": ntn,
        "strn": str(data.get("strn") or "").strip()[:50],
        "province": province,
        "registration_type": reg,
        "address": address[:500],
    }, None


def clean_product(data, uom_lookup):
    """Validate a product payload from the Products page / import."""
    description = " ".join(str(data.get("description") or "").split())
    if not description:
        return None, "Product name is required."
    uom = uom_lookup(data.get("uom"))
    if not uom:
        return None, "UoM is required."
    tax = to_decimal(data.get("default_tax_rate"))
    if tax is None:
        return None, "Tax rate is required."
    if tax < 0 or tax > 100:
        return None, "Tax rate must be between 0 and 100."
    rate = to_decimal(data.get("rate"), Decimal("0"))
    if rate < 0:
        return None, "Rate cannot be negative."
    hs_code = str(data.get("hs_code") or "").strip()
    if hs_code and not HS_CODE_RE.match(hs_code):
        return None, "HS code should look like 3004.9099."
    return {
        "description": description[:500],
        "hs_code": hs_code,
        "uom": uom,
        "rate": rate_key(rate),
        "default_tax_rate": tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "sale_type": str(data.get("sale_type") or "").strip(),
        "sro_schedule_no": str(data.get("sro_schedule_no") or "").strip(),
        "sro_item_serial_no": str(data.get("sro_item_serial_no") or "").strip(),
    }, None
