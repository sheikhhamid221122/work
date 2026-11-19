"""
API routes to support the form-based invoice creation
"""
from flask import request, jsonify, session
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from psycopg2.extras import Json

from fbr_reference_routes import (
    canonical_province,
    fetch_hs_code_description,
    fetch_provinces,
    fetch_transaction_types,
)
import advance_tax
import client_features
import master_data

SPECIAL_USERNAMES = {"H075895", "F667833", "infinityeng"}
# The default cap. A client whose sheet carries more is listed in
# client_features.py, so read it through `client_features.custom_fields_max`
# wherever a client is in hand rather than using this directly.
INVOICE_CUSTOM_FIELDS_MAX = client_features.DEFAULT_CUSTOM_FIELDS_MAX

# The paper clients whose invoice carries a "No. of Reams" column beside the
# quantity, and whose Products step therefore offers the field. It is a figure
# they want printed and nothing else: no total is derived from it, and app.py
# keeps it out of the FBR payload, whose item schema has no such field.
REAMS_USERNAMES = {
    "3520230962516",  # M/S Hannan Traders
    "3520212803454",  # AK International
    "3520224169621",  # Apple International
    "4242880",        # Paper Land
    "3520261094743",  # Paper Experts
}


# The clients whose invoice carries a "Sale Person" line, and whose Seller
# step therefore offers the field. Like the reams count it is a figure they
# want printed and nothing else -- no total is derived from it, and app.py
# keeps it out of the FBR payload, whose schema has no such field. The names
# typed here are remembered per client in clients.sales_persons so the same
# handful can be picked again instead of retyped.
SALES_PERSON_USERNAMES = {
    "3520223163127",  # Salon Marketings
}

# How many remembered names a client keeps. A shop has a few sales staff, not
# hundreds; a cap stops a typo-ridden list growing without bound.
SALES_PERSONS_MAX = 50
SALES_PERSON_MAX_LEN = 60


# Clients who file everything under SRO 297(I)/2023. Every line of every
# invoice of theirs carries the same schedule and serial and the same sale
# type, so the form fills and locks the fields and this module writes them
# again here. The form is a convenience; this is the rule. Without it a stale
# tab, a restored draft or anything that posts straight to the API could file
# a line with a blank or wrong SRO.
SRO_297_USERNAMES = {
    "3520223163127",  # Salon Marketings
}
SRO_297_SCHEDULE = "297(I)/2023-Table-I"
SRO_297_ITEM_SERIAL = "12"
# Exactly as FBR spells it in the transaction types list (DI API 5.5). The pipe
# is theirs, not a typo -- it must match scenarioToSaleType['SN024'] in
# create-invoice.html, or the dropdown and the payload disagree.
SRO_297_SALE_TYPE = "Goods as per SRO.297(|)/2023"


def _is_special_username(username):
    return (username or "").strip() in SPECIAL_USERNAMES


def _wants_sro_297(username):
    return (username or "").strip() in SRO_297_USERNAMES


def _wants_sales_person(username):
    return (username or "").strip() in SALES_PERSON_USERNAMES


def _normalize_sales_person(value):
    """One sales person's name as it should print, or "" for none."""
    return " ".join(str(value if value is not None else "").split())[:SALES_PERSON_MAX_LEN]


def _normalize_sales_persons(values):
    """The remembered list: trimmed, de-duplicated case-insensitively, capped.

    Order is preserved so the list reads in the order the client built it
    rather than being re-sorted under them between invoices.
    """
    if not isinstance(values, (list, tuple)):
        return []
    seen = set()
    names = []
    for value in values:
        name = _normalize_sales_person(value)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
        if len(names) >= SALES_PERSONS_MAX:
            break
    return names


def _column_exists(cur, table, column):
    """Whether a column is actually present.

    The migration that adds it is applied by hand on the live database, so a
    deploy can run ahead of it. Every read of these columns is guarded, which
    turns "the schema is behind" into the feature being quietly unavailable
    instead of a 500 on a page everyone uses.
    """
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return cur.fetchone() is not None


# The buyer columns that a hand-applied migration adds, in the order they are
# selected. Each is printed on one or more clients' replicas, none is an FBR
# field, and each is read only when it is actually there -- so an app deployed
# ahead of its migration offers the field to nobody instead of 500ing the
# Buyers page. Normalisers keep what is stored to the shape the sheet prints.
OPTIONAL_BUYER_COLUMNS = ("phone", "supplier_number", "delivery_address")


def _optional_buyer_columns(cur):
    """Which of OPTIONAL_BUYER_COLUMNS this database actually has."""
    return tuple(name for name in OPTIONAL_BUYER_COLUMNS
                 if _column_exists(cur, "buyers", name))


def _buyer_select_list(present):
    """The SELECT fragment for the optional columns, absent ones as ''.

    Always the same arity, in the same order, so a caller can unpack the row
    positionally whatever the schema happens to hold.
    """
    return ", ".join(name if name in present else "''"
                     for name in OPTIONAL_BUYER_COLUMNS)


def _buyer_optional_values(row, offset):
    """The optional columns of a buyer row, as a dict keyed by column name."""
    return {name: (row[offset + index] or "")
            for index, name in enumerate(OPTIONAL_BUYER_COLUMNS)}


def _buyer_phone_supported(cur):
    return _column_exists(cur, "buyers", "phone")


def _buyer_supplier_fields_supported(cur):
    return (_column_exists(cur, "buyers", "supplier_number")
            and _column_exists(cur, "buyers", "delivery_address"))


# Public, because the Buyers page in master_data_routes.py asks the same
# questions and the answers must not be allowed to drift between the two
# pages.
buyer_phone_column_exists = _buyer_phone_supported
buyer_optional_columns = _optional_buyer_columns
buyer_select_list = _buyer_select_list
buyer_optional_values = _buyer_optional_values


def _wants_buyer_phone(cur, client_id):
    """Whether this client's invoice has a row for the buyer's phone number.

    Two sets answer this, for historical reasons worth keeping straight. The
    Salon Marketings build introduced the phone beside their Sale Person line
    and gated both on the one username set; Global Surface want the phone and
    have no Sale Person line, so the second set is keyed on the client id in
    client_features.py. Either is enough.
    """
    cur.execute(
        """
        SELECT u.username, u.id
        FROM users u
        JOIN clients c ON u.id = c.user_id
        WHERE c.id = %s
        """,
        (client_id,),
    )
    row = cur.fetchone()
    username = row[0] if row else None
    user_id = row[1] if row else None
    return (_wants_sales_person(username)
            or client_features.prints_buyer_phone(username, user_id, client_id))


def buyer_phone_enabled(cur, client_id):
    """Whether this client's Buyers page should offer the Phone field."""
    return _wants_buyer_phone(cur, client_id) and _buyer_phone_supported(cur)


def _wants_buyer_supplier_fields(cur, client_id):
    """Whether this client's replica prints a supplier number and delivery address."""
    cur.execute(
        """
        SELECT u.username, u.id
        FROM users u
        JOIN clients c ON u.id = c.user_id
        WHERE c.id = %s
        """,
        (client_id,),
    )
    row = cur.fetchone()
    return client_features.prints_buyer_supplier_fields(
        row[0] if row else None, row[1] if row else None, client_id)


def buyer_supplier_fields_enabled(cur, client_id):
    """Whether this client's Buyers page should offer the two vendor fields."""
    return (_wants_buyer_supplier_fields(cur, client_id)
            and _buyer_supplier_fields_supported(cur))


def _normalize_supplier_number(value):
    """A buyer's vendor number for this seller, as it should print.

    Free text, collapsed and capped. Buyers write them every which way --
    0000332375, 1006060 -- and this one is printed and matched by eye, never
    parsed, so rejecting a shape would only get in the way.
    """
    return " ".join(str(value if value is not None else "").split())[:40]


def _normalize_delivery_address(value):
    """Where the goods go, when that is not the buyer's billing address."""
    return " ".join(str(value if value is not None else "").split())[:200]


# Column -> how its value is cleaned before it is stored. Every optional
# buyer column must appear here, so a new one cannot be added without
# deciding what shape it is kept in.
_BUYER_COLUMN_NORMALIZERS = {
    "phone": lambda value: _normalize_phone(value),
    "supplier_number": _normalize_supplier_number,
    "delivery_address": _normalize_delivery_address,
}


def _normalize_phone(value):
    """A buyer's phone as typed, collapsed and capped. Never validated.

    Pakistani numbers are written half a dozen ways and this one is printed,
    never dialled or filed, so rejecting a shape would only get in the way.
    """
    return " ".join(str(value if value is not None else "").split())[:40]


def _wants_reams(username):
    return (username or "").strip() in REAMS_USERNAMES


def _normalize_part_unit(value):
    """The Part Unit figure for a line, or None when the box was left empty.

    None rather than 0 or "": an empty box means "no figure of my own", and
    the template answers that by deriving one the way it always has. A zero
    typed deliberately is kept and prints as a zero, which is why this is not
    written as a falsy check.

    A float, unlike the reams count beside it, because this one is a quantity
    the client's sheet prints to two decimals next to the Qty it usually
    equals -- not a label. Nothing is calculated from it either way.
    """
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(Decimal(text))
    except (ArithmeticError, ValueError):
        return None


def _normalize_reams(value):
    """The reams count as it should print, or "" for a line that has none.

    Carried as text rather than a number because the cell has to be *empty*
    when the client leaves the field empty -- a 0 printed in their reams
    column would read as a real count of none. Nothing is calculated from it,
    so there is no reason to hold it as a float.
    """
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return ""
    try:
        number = Decimal(text)
    except (ArithmeticError, ValueError):
        return ""
    if number <= 0:
        return ""
    return format(number.normalize(), "f")


def _normalize_tax_id(value):
    if value is None:
        return ""

    raw = str(value).strip().upper()
    digits_only = "".join(ch for ch in raw if ch.isdigit())
    if len(digits_only) == 13:
        return digits_only

    return "".join(ch for ch in raw if ch.isalnum())


def _require_valid_tax_id(value, label):
    normalized = _normalize_tax_id(value)
    if normalized.isdigit() and len(normalized) == 13:
        return normalized
    if len(normalized) == 7 and normalized.isalnum():
        return normalized
    raise ValueError(f"{label} must be 7 characters (NTN) or 13 digits (CNIC)")


def parse_rate_percent(rate):
    """
    Pull a Decimal percentage out of the many shapes a rate arrives in:
    "18%", "18.00%", "18", 18, 0.18 is NOT treated as 18% (callers send percent).
    Returns None when no percentage can be read.
    """
    if rate is None:
        return None
    text = str(rate).strip().replace("%", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def expected_sales_tax(value_excl, rate):
    """
    The sales tax FBR will re-compute for a straight percentage-of-value line:
    round(valueSalesExcludingST * rate / 100, 2) with HALF-UP rounding.

    Decimal is essential here. In binary floating point 694806.75 * 0.18 is
    125065.21499999999, so rounding to 2dp yields 125065.21, while FBR computes
    the exact decimal 125065.215 and expects 125065.22 -- a one-paisa mismatch
    that fails validation with "Provided sales tax amount does not match the
    calculated sales tax amount".
    """
    percent = parse_rate_percent(rate)
    if percent is None:
        return None
    try:
        base = Decimal(str(value_excl))
    except Exception:
        return None
    return (base * percent / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


# How far the submitted tax may sit from the recomputed figure and still be
# treated as a rounding artifact we should silently correct. A genuine
# difference (3rd schedule tax on retail price, SRO-driven amounts, exempt
# lines) is far larger than this and is left exactly as the user entered it.
SALES_TAX_SNAP_TOLERANCE = Decimal("0.05")


def _normalize_custom_field_names(fields, limit=None):
    names = []
    seen = set()
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except Exception:
            fields = []
    source = fields if isinstance(fields, list) else []
    for field in source[:limit or INVOICE_CUSTOM_FIELDS_MAX]:
        if isinstance(field, dict):
            name = field.get("name", "")
        else:
            name = field
        name = str(name or "").strip()
        key = name.lower()
        if name and key not in seen:
            names.append(name[:80])
            seen.add(key)
    return names


# Business Profiles / Buyers / Products / Invoice APIs
def add_invoice_form_routes(app, get_db_connection, get_env):
    # ---------------- Business Profiles ----------------
    @app.route("/api/business-profiles", methods=["GET"])
    def get_business_profiles():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, business_name, address, province, ntn_cnic, strn, is_default 
            FROM business_profiles 
            WHERE client_id = %s 
            ORDER BY is_default DESC, business_name
            """,
            (client_id,),
        )

        profiles = [
            {
                "id": row[0],
                "business_name": row[1],
                "address": row[2],
                "province": row[3],
                "ntn_cnic": row[4],
                "strn": row[5],
                "is_default": row[6],
            }
            for row in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify(profiles)

    @app.route("/api/business-profiles", methods=["POST"])
    def create_business_profile():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        data = request.get_json()
        required_fields = ["business_name", "address", "province", "ntn_cnic"]
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        try:
            data["ntn_cnic"] = _require_valid_tax_id(data["ntn_cnic"], "Seller NTN/CNIC")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        if data.get("is_default", False):
            cur.execute(
                "UPDATE business_profiles SET is_default = FALSE WHERE client_id = %s",
                (client_id,),
            )

        cur.execute(
            """
            INSERT INTO business_profiles 
              (client_id, business_name, address, province, ntn_cnic, strn, is_default) 
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                client_id,
                data["business_name"],
                data["address"],
                data["province"],
                data["ntn_cnic"],
                data.get("strn", ""),
                data.get("is_default", False),
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"id": new_id, "message": "Business profile created successfully"})

    @app.route("/api/business-profiles/<int:profile_id>", methods=["PUT"])
    def update_business_profile(profile_id):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        data = request.get_json() or {}
        if "ntn_cnic" in data:
            try:
                data["ntn_cnic"] = _require_valid_tax_id(data["ntn_cnic"], "Seller NTN/CNIC")
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM business_profiles WHERE id = %s AND client_id = %s",
            (profile_id, client_id),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": "Business profile not found or access denied"}), 404

        if data.get("is_default", False):
            cur.execute(
                "UPDATE business_profiles SET is_default = FALSE WHERE client_id = %s",
                (client_id,),
            )

        fields = [
            "business_name",
            "address",
            "province",
            "ntn_cnic",
            "strn",
            "is_default",
        ]
        updates = [f"{f} = %s" for f in fields if f in data]
        if updates:
            values = [data[f] for f in fields if f in data]
            values.extend([profile_id, client_id])
            cur.execute(
                f"""
                UPDATE business_profiles
                SET {', '.join(updates)}, updated_at = NOW()
                WHERE id = %s AND client_id = %s
                """,
                values,
            )

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "Business profile updated successfully"})

    # NEW: Delete business profile
    @app.route("/api/business-profiles/<int:profile_id>", methods=["DELETE"])
    def delete_business_profile(profile_id):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Pre-check: block deletion if referenced by any draft invoices
            cur.execute(
                """
                SELECT 1 FROM invoice_drafts
                WHERE seller_profile_id = %s AND client_id = %s LIMIT 1
                """,
                (profile_id, client_id),
            )
            if cur.fetchone():
                cur.close(); conn.close()
                return (
                    jsonify({
                        "error": "in-use",
                        "message": "This business profile is used in existing draft invoices and cannot be deleted. Remove or modify those drafts first.",
                    }),
                    409,
                )

            cur.execute(
                """
                DELETE FROM business_profiles
                WHERE id = %s AND client_id = %s
                RETURNING id
                """,
                (profile_id, client_id),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return jsonify({"error": "Business profile not found"}), 404
            return jsonify({"success": True, "deleted_id": profile_id})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            cur.close(); conn.close()
    # NEW: Set business profile default
    @app.route("/api/business-profiles/<int:profile_id>/default", methods=["POST"])
    def set_business_profile_default(profile_id):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Ensure ownership
            cur.execute(
                "SELECT id FROM business_profiles WHERE id = %s AND client_id = %s",
                (profile_id, client_id),
            )
            if not cur.fetchone():
                cur.close(); conn.close()
                return jsonify({"error": "Business profile not found"}), 404
            # Clear existing defaults then set new
            cur.execute(
                "UPDATE business_profiles SET is_default = FALSE WHERE client_id = %s",
                (client_id,),
            )
            cur.execute(
                "UPDATE business_profiles SET is_default = TRUE WHERE id = %s AND client_id = %s",
                (profile_id, client_id),
            )
            conn.commit()
            return jsonify({"success": True, "default_id": profile_id})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            cur.close(); conn.close()
    # ---------------- Buyers ----------------
    @app.route("/api/buyers", methods=["GET"])
    def get_buyers():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        # The optional columns are selected only where they exist, so a deploy
        # that runs ahead of their migration still lists buyers instead of
        # 500ing. Absent ones come back as '' and the arity never changes.
        present = _optional_buyer_columns(cur)
        cur.execute(
            f"""
            SELECT id, business_name, address, province, ntn_cnic, strn, registration_type, buyer_code, is_default,
                   {_buyer_select_list(present)}
            FROM buyers
            WHERE client_id = %s
            ORDER BY is_default DESC, business_name
            """,
            (client_id,),
        )
        buyers = [
            dict(
                {
                    "id": r[0],
                    "business_name": r[1],
                    "address": r[2],
                    "province": r[3],
                    "ntn_cnic": r[4],
                    "strn": r[5],
                    "registration_type": r[6],
                    "buyer_code": r[7],
                    "is_default": r[8],
                },
                **_buyer_optional_values(r, 9)
            )
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify(buyers)

    @app.route("/api/buyers", methods=["POST"])
    def create_buyer():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        data = request.get_json()
        required_fields = ["business_name", "address", "province", "ntn_cnic"]
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        try:
            data["ntn_cnic"] = _require_valid_tax_id(data["ntn_cnic"], "Buyer NTN/CNIC")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Same buyer twice (same NTN/CNIC, or same name) is refused, and a
            # code is assigned when none was typed -- see master_data.py.
            existing = master_data.load_buyer_keys(cur, client_id)
            clash = master_data.find_duplicate_buyer(existing, data["business_name"], data["ntn_cnic"])
            if clash:
                return jsonify({
                    "error": master_data.buyer_duplicate_message(clash),
                    "duplicate": {"field": clash[0], "id": clash[1][0]},
                }), 409

            buyer_code = str(data.get("buyer_code") or "").strip()
            if buyer_code:
                owner = master_data.find_buyer_code_owner(existing, buyer_code)
                if owner:
                    return jsonify({"error": f"Buyer code {buyer_code} is already used by {owner[1]}."}), 409
            else:
                buyer_code = master_data.reserve_codes(
                    cur, "buyers", "buyer_code", master_data.BUYER_CODE_PREFIX, client_id)[0]

            if data.get("is_default", False):
                cur.execute(
                    "UPDATE buyers SET is_default = FALSE WHERE client_id = %s",
                    (client_id,),
                )
            # Only the optional columns this database has, each cleaned by its
            # own normaliser. A column the migration has not added yet is
            # simply not written.
            present = _optional_buyer_columns(cur)
            extra_values = tuple(
                _BUYER_COLUMN_NORMALIZERS[name](data.get(name)) for name in present)
            extra_names = "".join(", " + name for name in present)
            extra_slots = ",%s" * len(present)
            cur.execute(
                f"""
                INSERT INTO buyers
                  (client_id, business_name, address, province, ntn_cnic, strn,
                   registration_type, buyer_code, is_default{extra_names})
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s{extra_slots})
                RETURNING id
                """,
                (
                    client_id,
                    data["business_name"],
                    data["address"],
                    data["province"],
                    data["ntn_cnic"],
                    data.get("strn", ""),
                    data.get("registration_type", "Unregistered"),
                    buyer_code,
                    data.get("is_default", False),
                ) + extra_values,
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            return jsonify({"id": new_id, "buyer_code": buyer_code, "message": "Buyer created successfully"})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": f"Failed to create buyer: {str(e)}"}), 500
        finally:
            cur.close()
            conn.close()

    # UPDATE: Edit buyer
    @app.route("/api/buyers/<int:buyer_id>", methods=["PUT"])
    def update_buyer(buyer_id):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        data = request.get_json() or {}
        
        # Validate required fields if provided
        if "ntn_cnic" in data and data["ntn_cnic"]:
            try:
                data["ntn_cnic"] = _require_valid_tax_id(data["ntn_cnic"], "Buyer NTN/CNIC")
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Check if buyer exists and belongs to this client
            cur.execute(
                "SELECT id FROM buyers WHERE id = %s AND client_id = %s",
                (buyer_id, client_id),
            )
            if not cur.fetchone():
                return jsonify({"error": "Buyer not found"}), 404

            # An edit must not turn this buyer into a copy of another one.
            existing = master_data.load_buyer_keys(cur, client_id)
            current = next(r for r in existing if r[0] == buyer_id)
            clash = master_data.find_duplicate_buyer(
                existing,
                data.get("business_name", current[1]),
                data.get("ntn_cnic", current[2]),
                exclude_id=buyer_id,
            )
            if clash:
                return jsonify({
                    "error": master_data.buyer_duplicate_message(clash),
                    "duplicate": {"field": clash[0], "id": clash[1][0]},
                }), 409

            # Every buyer keeps a code: a blank one in the payload (the invoice
            # form sends whatever its code box holds) keeps the current code,
            # or assigns one if the buyer never had any.
            if "buyer_code" in data:
                new_code = str(data.get("buyer_code") or "").strip()
                if not new_code:
                    new_code = str(current[3] or "").strip() or master_data.reserve_codes(
                        cur, "buyers", "buyer_code", master_data.BUYER_CODE_PREFIX, client_id)[0]
                else:
                    owner = master_data.find_buyer_code_owner(existing, new_code, exclude_id=buyer_id)
                    if owner:
                        return jsonify({"error": f"Buyer code {new_code} is already used by {owner[1]}."}), 409
                data["buyer_code"] = new_code

            # Handle is_default flag - if setting this buyer as default, unset others
            if data.get("is_default", False):
                cur.execute(
                    "UPDATE buyers SET is_default = FALSE WHERE client_id = %s",
                    (client_id,),
                )

            # Build dynamic update query
            fields = [
                "business_name",
                "address",
                "province",
                "ntn_cnic",
                "strn",
                "registration_type",
                "buyer_code",
                "is_default",
            ]
            fields.extend(_optional_buyer_columns(cur))
            updates = []
            values = []
            for field in fields:
                if field in data:
                    updates.append(f"{field} = %s")
                    value = data[field] if data[field] is not None else ""
                    normalize = _BUYER_COLUMN_NORMALIZERS.get(field)
                    values.append(normalize(value) if normalize else value)
            
            if not updates:
                return jsonify({"error": "No fields to update"}), 400

            values.extend([buyer_id, client_id])
            cur.execute(
                f"""
                UPDATE buyers
                SET {', '.join(updates)}
                WHERE id = %s AND client_id = %s
                RETURNING id
                """,
                values,
            )
            updated = cur.fetchone()
            conn.commit()
            
            if not updated:
                return jsonify({"error": "Buyer update failed"}), 404
            
            return jsonify({"id": updated[0], "message": "Buyer updated successfully"})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": f"Failed to update buyer: {str(e)}"}), 500
        finally:
            cur.close()
            conn.close()

    # DELETE: Remove buyer
    @app.route("/api/buyers/<int:buyer_id>", methods=["DELETE"])
    def delete_buyer(buyer_id):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                DELETE FROM buyers
                WHERE id = %s AND client_id = %s
                RETURNING id
                """,
                (buyer_id, client_id),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return jsonify({"error": "Buyer not found"}), 404
            return jsonify({"success": True, "deleted_id": buyer_id})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            cur.close(); conn.close()
    # ---------------- Products (with soft delete) ----------------
    @app.route("/api/products", methods=["GET"])
    def get_products():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Determine username for gating
            cur.execute(
                """
                SELECT u.username 
                FROM users u
                JOIN clients c ON u.id = c.user_id
                WHERE c.id = %s
                """,
                (client_id,),
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "User not found"}), 404
            username = (row[0] or "").strip()
            is_special_user = _is_special_username(username)

            # Discover optional product columns
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'products'
                """
            )
            available_columns = {r[0] for r in cur.fetchall()}
            has_product_code = "product_code" in available_columns
            has_sro_item_serial_no = "sro_item_serial_no" in available_columns

            # Codes are returned for everyone so the invoice form's product
            # picker can show and search them. Only the special-username
            # clients' form puts a code on the invoice line; that gate lives in
            # the page (isH075895User), not here.
            should_include_product_code = has_product_code

            select_exprs = [
                "id",
                "description",
                "hs_code",
                "rate",
                "uom",
                "default_tax_rate",
                "sro_schedule_no",
                "sale_type",
            ]

            if should_include_product_code:
                select_exprs.insert(3, "product_code")

            if has_sro_item_serial_no:
                select_exprs.append("sro_item_serial_no")

            cur.execute(
                f"""
                SELECT {', '.join(select_exprs)}
                FROM products
                WHERE client_id = %s
                  AND (is_active = TRUE OR is_active IS NULL)
                ORDER BY description
                """,
                (client_id,),
            )

            columns = [desc[0] for desc in cur.description]
            products = []
            for row in cur.fetchall():
                record = dict(zip(columns, row))
                product = {
                    "id": record["id"],
                    "description": record.get("description") or "",
                    "hs_code": record.get("hs_code") or "",
                    "rate": float(record.get("rate") or 0),
                    "uom": record.get("uom") or "",
                    "default_tax_rate": float(record.get("default_tax_rate") or 0),
                    "sro_schedule_no": record.get("sro_schedule_no") or "",
                    "sale_type": record.get("sale_type") or "",
                }

                if should_include_product_code:
                    product["product_code"] = record.get("product_code") or ""
                else:
                    product["product_code"] = ""

                if has_sro_item_serial_no:
                    product["sro_item_serial_no"] = record.get("sro_item_serial_no") or ""

                products.append(product)

            return jsonify(products)
        finally:
            cur.close()
            conn.close()

    @app.route("/api/products", methods=["POST"])
    def create_product():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json() or {}
        description = (data.get("description") or "").strip()
        if not description:
            return jsonify({"error": "Product description is required"}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Discover optional columns for backwards compatibility
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'products'
                """
            )
            available_columns = {r[0] for r in cur.fetchall()}
            has_product_code = "product_code" in available_columns
            has_sro_item_serial_no = "sro_item_serial_no" in available_columns

            # Duplicates. A caller that sends a rate (the product forms) gets the
            # name + rate rule from master_data: a clash is refused, and a
            # soft-deleted match is restored instead of copied. Callers that
            # send no rate (the invoice form saving line items, "add custom
            # product") keep the original behaviour: any product with that
            # name is reused, so they never create a copy either.
            strict = "rate" in data
            if strict:
                product_rows = master_data.load_product_keys(cur, client_id)
                clash = master_data.find_duplicate_product(product_rows, description, data.get("rate"))
                if clash:
                    return jsonify({
                        "error": master_data.product_duplicate_message(clash),
                        "duplicate": {"id": clash[0]},
                    }), 409
                deleted = master_data.find_duplicate_product(
                    product_rows, description, data.get("rate"), active=False)
                existing = (deleted[0], False) if deleted else None
            else:
                # Case-insensitive match to prevent duplicates
                cur.execute(
                    """
                    SELECT id, is_active FROM products
                    WHERE client_id = %s AND LOWER(description) = LOWER(%s)
                    """,
                    (client_id, description),
                )
                existing = cur.fetchone()
            if existing:
                if existing[1] is False:
                    cur.execute(
                        "UPDATE products SET is_active = TRUE WHERE id = %s",
                        (existing[0],),
                    )
                    conn.commit()
                    if strict:
                        return jsonify({
                            "id": existing[0],
                            "restored": True,
                            "message": "This product had been deleted; it has been restored.",
                        }), 200
                return jsonify({"id": existing[0], "message": "Product already exists"}), 200

            # Determine username for user-specific behavior
            cur.execute(
                """
                SELECT u.username 
                FROM users u
                JOIN clients c ON u.id = c.user_id
                WHERE c.id = %s
                """,
                (client_id,),
            )
            row = cur.fetchone()
            username = (row[0] or "").strip() if row else None
            is_special_user = _is_special_username(username)
            # Whether the Code box on the products page is theirs to fill in.
            # The special usernames have always been; client_features adds
            # the clients who type their own codes but still use the sale
            # type and SRO fields that `is_special_user` blanks below.
            own_product_code = is_special_user or client_features.types_own_product_code(
                username, client_id, session.get("user_id"))

            # Normalize numeric inputs
            try:
                rate_value = float(data.get("rate", 0))
            except (TypeError, ValueError):
                rate_value = 0.0

            try:
                default_tax_rate = float(data.get("default_tax_rate", 0))
            except (TypeError, ValueError):
                default_tax_rate = 0.0

            payload_sale_type = (data.get("sale_type") or "").strip()
            payload_sro_schedule = (data.get("sro_schedule_no") or "").strip()
            payload_sro_item = (data.get("sro_item_serial_no") or "").strip()

            if is_special_user:
                payload_sale_type = ""
                payload_sro_schedule = ""
                payload_sro_item = ""

            columns = [
                "client_id",
                "description",
                "hs_code",
                "rate",
                "uom",
                "default_tax_rate",
                "sro_schedule_no",
                "sale_type",
            ]
            values = [
                client_id,
                description,
                data.get("hs_code", ""),
                rate_value,
                data.get("uom", ""),
                default_tax_rate,
                payload_sro_schedule,
                payload_sale_type,
            ]

            # Special-username clients type their own product codes (they print
            # on their invoices); everyone else gets the next P-#### code.
            product_code = ""
            if has_product_code:
                if own_product_code:
                    product_code = (data.get("product_code") or "").strip()
                else:
                    product_code = master_data.reserve_codes(
                        cur, "products", "product_code", master_data.PRODUCT_CODE_PREFIX, client_id)[0]
                columns.insert(3, "product_code")
                values.insert(3, product_code)

            if has_sro_item_serial_no:
                columns.append("sro_item_serial_no")
                values.append(payload_sro_item)

            placeholders = ", ".join(["%s"] * len(values))
            cur.execute(
                f"""
                INSERT INTO products ({', '.join(columns)}, is_active)
                VALUES ({placeholders}, TRUE)
                RETURNING id
                """,
                values,
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            return jsonify({"id": new_id, "product_code": product_code, "message": "Product created successfully"})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": f"Failed to create product: {str(e)}"}), 500
        finally:
            cur.close()
            conn.close()

    @app.route("/api/products/<int:product_id>", methods=["PUT"])
    def update_product(product_id):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json() or {}
        description = (data.get("description") or "").strip()
        uom = (data.get("uom") or "").strip()
        if not description or not uom:
            return jsonify({"error": "Description and UoM are required"}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id
                FROM products
                WHERE id = %s
                  AND client_id = %s
                  AND (is_active = TRUE OR is_active IS NULL)
                """,
                (product_id, client_id),
            )
            if not cur.fetchone():
                return jsonify({"error": "Product not found"}), 404

            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'products'
                """
            )
            available_columns = {r[0] for r in cur.fetchall()}
            has_product_code = "product_code" in available_columns
            has_sro_item_serial_no = "sro_item_serial_no" in available_columns

            cur.execute(
                """
                SELECT u.username
                FROM users u
                JOIN clients c ON u.id = c.user_id
                WHERE c.id = %s
                """,
                (client_id,),
            )
            row = cur.fetchone()
            username = (row[0] or "").strip() if row else None
            is_special_user = _is_special_username(username)
            own_product_code = is_special_user or client_features.types_own_product_code(
                username, client_id, session.get("user_id"))

            def safe_float(val):
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 0.0

            rate_value = safe_float(data.get("rate", 0))
            default_tax_rate = safe_float(data.get("default_tax_rate", 0))
            payload_sale_type = (data.get("sale_type") or "").strip()
            payload_sro_schedule = (data.get("sro_schedule_no") or "").strip()
            payload_sro_item = (data.get("sro_item_serial_no") or "").strip()

            if is_special_user:
                payload_sale_type = ""
                payload_sro_schedule = ""
                payload_sro_item = ""

            updates = []
            values = []

            def add_update(column, value):
                updates.append(f"{column} = %s")
                values.append(value)

            add_update("description", description)
            add_update("hs_code", (data.get("hs_code") or "").strip())
            add_update("rate", rate_value)
            add_update("uom", uom)
            add_update("default_tax_rate", default_tax_rate)
            add_update("sro_schedule_no", payload_sro_schedule)
            add_update("sale_type", payload_sale_type)

            if has_sro_item_serial_no:
                add_update("sro_item_serial_no", payload_sro_item)

            if has_product_code:
                if own_product_code:
                    add_update("product_code", (data.get("product_code") or "").strip())
                else:
                    # Assigned codes are not edited; the invoice form's hidden
                    # code box sends "" and must not wipe them. A product that
                    # predates codes gets one on its first save.
                    cur.execute("SELECT product_code FROM products WHERE id = %s", (product_id,))
                    current_code = ((cur.fetchone() or [""])[0] or "").strip()
                    if not current_code:
                        add_update("product_code", master_data.reserve_codes(
                            cur, "products", "product_code", master_data.PRODUCT_CODE_PREFIX, client_id)[0])

            # An edit must not turn this product into a copy of another one.
            clash = master_data.find_duplicate_product(
                master_data.load_product_keys(cur, client_id), description, rate_value,
                exclude_id=product_id)
            if clash:
                return jsonify({
                    "error": master_data.product_duplicate_message(clash),
                    "duplicate": {"id": clash[0]},
                }), 409

            if not updates:
                return jsonify({"error": "No fields to update"}), 400

            values.extend([product_id, client_id])
            cur.execute(
                f"""
                UPDATE products
                SET {', '.join(updates)}
                WHERE id = %s AND client_id = %s
                RETURNING id
                """,
                values,
            )
            updated = cur.fetchone()
            conn.commit()
            if not updated:
                return jsonify({"error": "Product update failed"}), 404
            return jsonify({"id": updated[0], "message": "Product updated successfully"})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": f"Failed to update product: {str(e)}"}), 500
        finally:
            cur.close()
            conn.close()

    @app.route("/api/products/<int:product_id>", methods=["DELETE"])
    def delete_product(product_id):
        """
        Soft delete a product: mark is_active = FALSE for this client's product.
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE products
                SET is_active = FALSE
                WHERE id = %s
                  AND client_id = %s
                  AND (is_active = TRUE OR is_active IS NULL)
                RETURNING id
                """,
                (product_id, client_id),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return jsonify({"error": "Product not found"}), 404
            return jsonify({"success": True, "soft_deleted_id": product_id})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            cur.close()
            conn.close()

    # ---------------- User Product Settings ----------------
    @app.route("/api/user-product-settings", methods=["GET"])
    def get_user_product_settings():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.username 
            FROM users u
            JOIN clients c ON u.id = c.user_id
            WHERE c.id = %s
            """,
            (client_id,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "User not found"}), 404
        username = row[0]
        cur.close()
        conn.close()
        # Enabled for all users
        return jsonify({"useProductDropdown": True, "username": username})

    # ---------------- Sales persons ----------------
    # Only the clients in SALES_PERSON_USERNAMES print a "Sale Person" line,
    # so only they are offered the field. For everyone else these endpoints
    # answer "disabled, empty list" and nothing in their form changes.

    def _sales_persons_state(cur, client_id):
        """(enabled, names) for this client, tolerant of a missing column."""
        cur.execute(
            """
            SELECT u.username
            FROM users u
            JOIN clients c ON u.id = c.user_id
            WHERE c.id = %s
            """,
            (client_id,),
        )
        row = cur.fetchone()
        enabled = _wants_sales_person(row[0] if row else None)
        if not enabled or not _column_exists(cur, "clients", "sales_persons"):
            return enabled, []
        cur.execute("SELECT sales_persons FROM clients WHERE id = %s", (client_id,))
        row = cur.fetchone()
        return enabled, _normalize_sales_persons(row[0] if row else [])

    def _remember_sales_person(cur, client_id, name):
        """Add a newly typed name to this client's remembered list.

        Called from the invoice create path, which is the whole point of the
        feature: the client types a name once and picks it from then on. A
        name already on the list is left exactly where it is -- reordering it
        would move the entries around under someone mid-invoice.
        """
        if not name or not _column_exists(cur, "clients", "sales_persons"):
            return
        cur.execute("SELECT sales_persons FROM clients WHERE id = %s", (client_id,))
        row = cur.fetchone()
        names = _normalize_sales_persons(row[0] if row else [])
        if any(existing.lower() == name.lower() for existing in names):
            return
        cur.execute(
            "UPDATE clients SET sales_persons = %s WHERE id = %s",
            (Json(_normalize_sales_persons(names + [name])), client_id),
        )

    @app.route("/api/sales-persons", methods=["GET"])
    def get_sales_persons():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            enabled, names = _sales_persons_state(cur, client_id)
            return jsonify({"enabled": enabled, "salesPersons": names})
        finally:
            cur.close()
            conn.close()

    @app.route("/api/sales-persons", methods=["PUT"])
    def update_sales_persons():
        """Replace the remembered list -- used to correct or remove a name.

        Adding one is not done here: a name is remembered automatically when
        an invoice is created with it.
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        data = request.get_json() or {}
        names = _normalize_sales_persons(data.get("salesPersons") or [])

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            enabled, _ = _sales_persons_state(cur, client_id)
            if not enabled:
                return jsonify({"error": "Sales persons are not enabled for this account"}), 403
            if not _column_exists(cur, "clients", "sales_persons"):
                return jsonify({"error": "Database is missing clients.sales_persons -- "
                                         "run scripts/migrate.py"}), 503
            cur.execute(
                "UPDATE clients SET sales_persons = %s WHERE id = %s RETURNING id",
                (Json(names), client_id),
            )
            if not cur.fetchone():
                conn.rollback()
                return jsonify({"error": "Client not found"}), 404
            conn.commit()
            return jsonify({"success": True, "salesPersons": names})
        except Exception as exc:
            conn.rollback()
            return jsonify({"error": f"Failed to save sales persons: {exc}"}), 500
        finally:
            cur.close()
            conn.close()

    # ---------------- Invoice Custom Field Defaults ----------------
    def _custom_fields_max(cur, client_id):
        """How many custom field pairs this client's invoice has room for.

        Whatever client_features.CUSTOM_FIELD_CAPS allows them, two for
        everyone else. The lookup is here rather than in the caller so the
        GET and the PUT below cannot disagree about the cap and leave a
        client's saved defaults silently truncated on the next read.
        """
        cur.execute(
            """
            SELECT u.username, u.id
            FROM users u
            JOIN clients c ON u.id = c.user_id
            WHERE c.id = %s
            """,
            (client_id,),
        )
        row = cur.fetchone()
        return client_features.custom_fields_max(
            row[0] if row else None, row[1] if row else None, client_id)

    @app.route("/api/invoice-custom-field-defaults", methods=["GET"])
    def get_invoice_custom_field_defaults():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT invoice_custom_field_names
                FROM clients
                WHERE id = %s
                """,
                (client_id,),
            )
            row = cur.fetchone()
            names = _normalize_custom_field_names(
                row[0] if row else [], _custom_fields_max(cur, client_id))
            return jsonify({
                "customFieldNames": names,
                "customFields": [{"name": name, "value": ""} for name in names],
            })
        finally:
            cur.close()
            conn.close()

    @app.route("/api/invoice-custom-field-defaults", methods=["PUT"])
    def update_invoice_custom_field_defaults():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json() or {}

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            names = _normalize_custom_field_names(
                data.get("customFieldNames") or data.get("customFields") or [],
                _custom_fields_max(cur, client_id),
            )
            cur.execute(
                """
                UPDATE clients
                SET invoice_custom_field_names = %s
                WHERE id = %s
                RETURNING id
                """,
                (Json(names), client_id),
            )
            if not cur.fetchone():
                conn.rollback()
                return jsonify({"error": "Client not found"}), 404
            conn.commit()
            return jsonify({
                "success": True,
                "customFieldNames": names,
                "customFields": [{"name": name, "value": ""} for name in names],
            })
        except Exception as e:
            conn.rollback()
            return jsonify({"error": f"Failed to save custom field defaults: {str(e)}"}), 500
        finally:
            cur.close()
            conn.close()

    # ---------------- Form Options ----------------
    @app.route("/api/form-options", methods=["GET"])
    def get_form_options():
        # Provinces come from FBR DI Reference API 5.1 (/pdi/v1/provinces), cached
        # for 24h, with a static fallback only if FBR is unreachable. The value we
        # send back is FBR's own stateProvinceDesc, since that exact string is what
        # gets posted as sellerProvince / buyerProvince.
        fbr_provinces, provinces_source = fetch_provinces(get_db_connection, get_env)
        provinces_data = [
            {
                "value": p["stateProvinceDesc"],
                "label": p["stateProvinceDesc"],
                "code": p.get("stateProvinceCode"),
            }
            for p in fbr_provinces
        ]


        # Sale types come from FBR DI Reference API 5.5 (/pdi/v1/transtypecode),
        # cached for 24h, with the old Section-9 static list as fallback only if
        # FBR is unreachable. "code" carries transactioN_TYPE_ID so the tax-rate
        # dropdown (5.8 SaleTypeToRate) can look up rates for the chosen sale
        # type without a second round-trip to resolve the ID.
        fbr_sale_types, sale_types_source = fetch_transaction_types(get_db_connection, get_env)
        sale_types_data = [
            {
                "value": t["transactioN_DESC"],
                "label": t["transactioN_DESC"],
                "code": t.get("transactioN_TYPE_ID"),
            }
            for t in fbr_sale_types
        ]

        return jsonify(
            {
                "invoiceTypes": [
                    {"value": "Sale Invoice", "label": "Sale Invoice"},
                    {"value": "Credit Note", "label": "Credit Note"},
                    {"value": "Debit Note", "label": "Debit Note"},
                ],
                "provinces": provinces_data,
                "provincesSource": provinces_source,
                # Registration types per FBR doc - only 2 valid values for buyerRegistrationType
                "registrationTypes": [
                    {"value": "Registered", "label": "Registered"},
                    {"value": "Unregistered", "label": "Unregistered"},
                ],
                "saleTypes": sale_types_data,
                "saleTypesSource": sale_types_source,
                # Extended UOM list - fallback values if FBR API unavailable
                # Dynamic UOMs are fetched via /api/reference/uoms and /api/reference/hs-uom
                "uoms": [
                    {"value": "Numbers, pieces, units", "label": "Numbers, pieces, units"},
                    {"value": "KG", "label": "KG - Kilogram"},
                    {"value": "MT", "label": "MT - Metric Ton"},
                    {"value": "LTR", "label": "LTR - Liter"},
                    {"value": "KWH", "label": "KWH - Kilowatt Hour"},
                    {"value": "MTR", "label": "MTR - Meter"},
                    {"value": "Square Metre", "label": "Square Metre"},
                    {"value": "CFT", "label": "CFT - Cubic Feet"},
                    {"value": "SFT", "label": "SFT - Square Feet"},
                    {"value": "PAIRS", "label": "PAIRS"},
                    {"value": "DOZEN", "label": "DOZEN"},
                    {"value": "GROSS", "label": "GROSS"},
                    {"value": "SET", "label": "SET"},
                    {"value": "PACK", "label": "PACK"},
                    {"value": "REAM", "label": "REAM"},
                    {"value": "ROLL", "label": "ROLL"},
                    {"value": "SHEET", "label": "SHEET"},
                    {"value": "TON", "label": "TON"},
                    {"value": "YARD", "label": "YARD"},
                    {"value": "FEET", "label": "FEET"},
                    {"value": "INCH", "label": "INCH"},
                    {"value": "CM", "label": "CM - Centimeter"},
                    {"value": "MM", "label": "MM - Millimeter"},
                    {"value": "GRAM", "label": "GRAM"},
                    {"value": "ML", "label": "ML - Milliliter"},
                    {"value": "GALLON", "label": "GALLON"},
                    {"value": "BARREL", "label": "BARREL"},
                    {"value": "BAG", "label": "BAG"},
                    {"value": "BOX", "label": "BOX"},
                    {"value": "CARTON", "label": "CARTON"},
                    {"value": "BOTTLE", "label": "BOTTLE"},
                    {"value": "CAN", "label": "CAN"},
                    {"value": "DRUM", "label": "DRUM"},
                    {"value": "BUNDLE", "label": "BUNDLE"},
                    {"value": "BALE", "label": "BALE"},
                ],
                "scenarioIds": [
                    {"value": "SN001", "label": "SN001 - Goods at standard rate (default)"},
                    {"value": "SN002", "label": "SN002 - Goods at standard rate (default)"},
                    {"value": "SN003", "label": "SN003 - Steel melting and re-rolling"},
                    {"value": "SN004", "label": "SN004 - Ship breaking"},
                    {"value": "SN005", "label": "SN005 - Goods at Reduced Rate"},
                    {"value": "SN006", "label": "SN006 - Exempt goods"},
                    {"value": "SN007", "label": "SN007 - Goods at zero-rate"},
                    {"value": "SN008", "label": "SN008 - 3rd Schedule Goods"},
                    {"value": "SN009", "label": "SN009 - Cotton Ginners"},
                    {"value": "SN010", "label": "SN010 - Telecommunication services"},
                    {"value": "SN011", "label": "SN011 - Toll Manufacturing"},
                    {"value": "SN012", "label": "SN012 - Petroleum Products"},
                    {"value": "SN013", "label": "SN013 - Electricity Supply to Retailers"},
                    {"value": "SN014", "label": "SN014 - Gas to CNG stations"},
                    {"value": "SN015", "label": "SN015 - Mobile Phones"},
                    {"value": "SN016", "label": "SN016 - Processing/Conversion of Goods"},
                    {"value": "SN017", "label": "SN017 - Goods (FED in ST Mode)"},
                    {"value": "SN018", "label": "SN018 - Services (FED in ST Mode)"},
                    {"value": "SN019", "label": "SN019 - Services"},
                    {"value": "SN020", "label": "SN020 - Electric Vehicle"},
                    {"value": "SN021", "label": "SN021 - Cement/Concrete Block"},
                    {"value": "SN022", "label": "SN022 - Potassium Chlorate"},
                    {"value": "SN023", "label": "SN023 - CNG Sales"},
                    {"value": "SN024", "label": "SN024 - Goods as per SRO.297(I)/2023"},
                    {"value": "SN025", "label": "SN025 - Non-Adjustable Supplies"},
                    {"value": "SN026", "label": "SN026 - Goods at standard rate (default)"},
                    {"value": "SN027", "label": "SN027 - 3rd Schedule Goods"},
                    {"value": "SN028", "label": "SN028 - Goods at Reduced Rate"},
                ],
                "taxRates": [
                    {"value": "0", "label": "0%"},
                    {"value": "1.00%", "label": "1%"},
                    {"value": "2.00%", "label": "2%"},
                    {"value": "3.00%", "label": "3%"},
                    {"value": "4.00%", "label": "4%"},
                    {"value": "4.50%", "label": "4.5%"},
                    {"value": "5.00%", "label": "5%"},
                    {"value": "8.00%", "label": "8%"},
                    {"value": "10.00%", "label": "10%"},
                    {"value": "12.00%", "label": "12%"},
                    {"value": "13.00%", "label": "13%"},
                    {"value": "14.00%", "label": "14%"},
                    {"value": "15.00%", "label": "15%"},
                    {"value": "16.00%", "label": "16%"},
                    {"value": "17.00%", "label": "17%"},
                    {"value": "18.00%", "label": "18%"},
                    {"value": "19.00%", "label": "19%"},
                    {"value": "20.00%", "label": "20%"},
                    {"value": "24.00%", "label": "24%"},
                    {"value": "25.00%", "label": "25%"},
                ],
                # Flag to indicate dynamic HS/UOM features are available
                "dynamicReferenceDataEnabled": True,
                "referenceDataEndpoints": {
                    "hsCodes": "/api/reference/hs-codes",
                    "uoms": "/api/reference/uoms",
                    "hsUom": "/api/reference/hs-uom",
                    "validate": "/api/reference/validate-hs-uom"
                }
            }
        )

    # ---------------- Batch Import Products ----------------
    @app.route("/api/products/batch-import", methods=["POST"])
    def batch_import_products():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.username 
            FROM users u
            JOIN clients c ON u.id = c.user_id
            WHERE c.id = %s
            """,
            (client_id,),
        )
        row = cur.fetchone()
        username = row[0] if row else None

        data = request.get_json() or {}
        product_list = data.get("products", [])
        if not product_list:
            return jsonify({"error": "No products provided for import"}), 400

        results = {"imported": 0, "skipped": 0}
        # Same code rule as create_product: special-username clients keep
        # typing their own codes.
        assign_codes = master_data.product_code_column_exists(cur) and not _is_special_username(username)
        sro_schedule_no = "EIGHTH SCHEDULE Table 1" if username == "3075270" else ""
        sro_item_serial_no = "81" if username == "3075270" else ""

        for name in product_list:
            cur.execute(
                """
                SELECT id, is_active FROM products
                WHERE client_id = %s AND LOWER(description) = LOWER(%s)
                """,
                (client_id, name),
            )
            existing = cur.fetchone()
            if existing:
                # Reactivate if soft-deleted
                if existing[1] is False:
                    cur.execute(
                        "UPDATE products SET is_active = TRUE WHERE id = %s",
                        (existing[0],),
                    )
                    results["imported"] += 1
                else:
                    results["skipped"] += 1
                continue

            cur.execute(
                """
                INSERT INTO products
                  (client_id, description, hs_code, uom, default_tax_rate,
                   sale_type, sro_schedule_no, sro_item_serial_no, is_active)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s, TRUE)
                RETURNING id
                """,
                (
                    client_id,
                    name,
                    "",
                    "Numbers, pieces, units",
                    1,
                    "Goods at Reduced Rate",
                    sro_schedule_no,
                    sro_item_serial_no,
                ),
            )
            new_product_id = cur.fetchone()[0]
            if assign_codes:
                cur.execute(
                    "UPDATE products SET product_code = %s WHERE id = %s",
                    (master_data.reserve_codes(cur, "products", "product_code",
                                               master_data.PRODUCT_CODE_PREFIX, client_id)[0],
                     new_product_id),
                )
            results["imported"] += 1

        conn.commit()
        cur.close()
        conn.close()
        return jsonify(
            {
                "message": f'Imported {results["imported"]} products, skipped {results["skipped"]} existing products',
                "results": results,
            }
        )

    # ---------------- Invoice Creation ----------------
    @app.route("/api/invoice/create", methods=["POST"])
    def create_invoice_from_form():
        client_id = session.get("client_id")
        env = get_env()
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        data = request.get_json() or {}

        required_fields = ["invoiceType", "invoiceDate", "sellerData", "buyerData", "items"]
        for f in required_fields:
            if f not in data:
                return jsonify({"error": f"Missing required field: {f}"}), 400

        seller = data["sellerData"]
        for f in ["sellerBusinessName", "sellerAddress", "sellerProvince", "sellerNTNCNIC"]:
            if f not in seller:
                return jsonify({"error": f"Missing required seller field: {f}"}), 400

        buyer = data["buyerData"]
        for f in ["buyerBusinessName", "buyerAddress", "buyerProvince", "buyerNTNCNIC"]:
            if f not in buyer:
                return jsonify({"error": f"Missing required buyer field: {f}"}), 400

        try:
            seller["sellerNTNCNIC"] = _require_valid_tax_id(
                seller["sellerNTNCNIC"], "Seller NTN/CNIC"
            )
            buyer["buyerNTNCNIC"] = _require_valid_tax_id(
                buyer["buyerNTNCNIC"], "Buyer NTN/CNIC"
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if not data["items"]:
            return jsonify({"error": "At least one item is required"}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.username, u.id
            FROM users u
            JOIN clients c ON u.id = c.user_id
            WHERE c.id = %s
            """,
            (client_id,),
        )
        row = cur.fetchone()
        username = row[0] if row else None
        user_id = row[1] if row else None
        is_special_user = _is_special_username(username)
        wants_reams = _wants_reams(username)
        # Printed on the clients whose replica has a "Sale Person" line, and
        # remembered for them so the name can be picked next time. Nobody
        # else's invoice carries it, so nobody else's payload does either.
        # Both of this client's printed-only fields are gated on the server as
        # well as in the form. The form hides them for everyone else, but a
        # hidden field is a UI decision and this is the rule: a client whose
        # replica does not print them never has them on their invoice.
        prints_sales_person = _wants_sales_person(username)
        # The phone is a separate question from the Sale Person line: Global
        # Surface print one and not the other. Same rule either way -- the
        # form hides the input for everyone else, but a hidden input is a UI
        # decision, and a client whose replica does not print the number must
        # not end up with one on their invoice.
        prints_buyer_phone = (prints_sales_person
                              or client_features.prints_buyer_phone(
                                  username, user_id, client_id))
        # The buyer's vendor number and delivery plant, same rule again: the
        # form hides them for everyone else, but a hidden input is a UI
        # decision and a client whose replica does not print them must not end
        # up with either on their invoice.
        prints_buyer_supplier_fields = client_features.prints_buyer_supplier_fields(
            username, user_id, client_id)
        # Reference and Term of Sales: two rows on one client's sheet, kept
        # off everyone else's invoice for the same reason as the fields
        # above -- the form hides the inputs, and the server is what makes
        # that stick.
        prints_reference_fields = client_features.prints_invoice_reference_fields(
            username, user_id, client_id)
        # The HS code's description as FBR spells it, and the client's own
        # product code. Both are columns on one replica's items table and
        # neither is in FBR's item schema; app.py strips them on the way out.
        prints_hs_description = client_features.prints_hs_code_description(
            username, user_id, client_id)
        types_own_product_code = client_features.types_own_product_code(
            username, user_id, client_id)
        # Part Unit, where it is a figure the client types rather than one
        # derived from the line. Printed-only, gated here for the same reason
        # as the two above.
        types_part_unit = client_features.types_part_unit(
            username, user_id, client_id)
        fixed_sro_297 = _wants_sro_297(username)
        # Advance Tax u/s 236(h). The rate is the only part of the line that
        # is stored -- the amount is derived when the PDF is rendered. Gated
        # on the server for the same reason the two fields above are: the
        # form hides the input for everyone else, but a hidden input is a UI
        # decision, and a client whose sheet does not print the line must not
        # end up with a rate on their invoice. See advance_tax.py.
        advance_tax_percent = (
            advance_tax.normalize_percent(
                data.get("advanceTaxPercent"), advance_tax.DEFAULT_PERCENT
            )
            if advance_tax.prints_advance_tax(username)
            else None
        )
        sales_person = (
            _normalize_sales_person(data.get("salesPerson"))
            if prints_sales_person
            else ""
        )
        if sales_person:
            try:
                _remember_sales_person(cur, client_id, sales_person)
                conn.commit()
            except Exception as exc:
                # Remembering the name is a convenience. Failing to remember it
                # must never stop the invoice being filed.
                conn.rollback()
                print(f"[Invoice] Could not remember sales person {sales_person!r}: {exc}")
        cur.close()
        conn.close()
        
        # Helper to sanitize any string value - removes control characters that break JSON
        import re
        def sanitize_string(val):
            if not val:
                return ""
            s = str(val)
            # Replace double quotes with single quotes (FBR API doesn't handle escaped quotes well)
            s = s.replace('"', "'")
            # Remove/replace all control characters and problematic whitespace
            s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
            s = s.replace("\\r\\n", " ").replace("\\n", " ").replace("\\r", " ")
            s = s.replace("\t", " ")
            # Remove any other control characters (ASCII 0-31 except space)
            s = re.sub(r'[\x00-\x1f\x7f]', '', s)
            # Collapse multiple spaces into single space
            s = re.sub(r'\s+', ' ', s).strip()
            return s

        seller_address = sanitize_string(seller["sellerAddress"])
        buyer_address = sanitize_string(buyer["buyerAddress"])
        # Update the seller/buyer dicts so draft data also gets sanitized values
        seller["sellerAddress"] = seller_address
        buyer["buyerAddress"] = buyer_address

        # Custom fields: keep only valid non-empty name/value pairs, capped
        # at what this client's sheet has room for -- see
        # client_features.CUSTOM_FIELD_CAPS, two for everyone else. Gated
        # here and not only in the form, for the same reason as the two
        # printed-only fields above.
        custom_fields = []
        custom_fields_max = client_features.custom_fields_max(
            username, user_id, client_id)
        for field in (data.get("customFields") or [])[:custom_fields_max]:
            if not isinstance(field, dict):
                continue
            name = sanitize_string(field.get("name", ""))
            value = sanitize_string(field.get("value", ""))
            if name and value:
                custom_fields.append({"name": name, "value": value})

        sig_raw = data.get("signatureArea")
        if not isinstance(sig_raw, dict):
            sig_raw = {}
        sig_source = str(sig_raw.get("source") or "company").strip().lower()
        if sig_source not in ("company", "fbr_generated_copy", "custom"):
            sig_source = "company"
        signature_area = {
            "enabled": bool(sig_raw.get("enabled")),
            "source": sig_source,
            "customText": sanitize_string(str(sig_raw.get("customText") or "")[:500]),
        }

        # Map whatever province spelling we hold (legacy "KPK", "ISLAMABAD", ...)
        # onto the exact description FBR's provinces API returns.
        try:
            known_provinces, _ = fetch_provinces(get_db_connection, get_env)
        except Exception:
            known_provinces = None
        seller_province = canonical_province(
            sanitize_string(seller["sellerProvince"]), known_provinces
        )
        buyer_province = canonical_province(
            sanitize_string(buyer["buyerProvince"]), known_provinces
        )

        invoice_json = {
            "invoiceType": sanitize_string(data["invoiceType"]),
            "invoiceDate": data["invoiceDate"],
            "sellerNTNCNIC": seller["sellerNTNCNIC"],
            "sellerBusinessName": sanitize_string(seller["sellerBusinessName"]),
            "sellerProvince": seller_province,
            "sellerAddress": seller_address,
            "sellerSTRN": seller.get("sellerSTRN", ""),
            "buyerNTNCNIC": buyer["buyerNTNCNIC"],
            "buyerBusinessName": sanitize_string(buyer["buyerBusinessName"]),
            "buyerProvince": buyer_province,
            "buyerAddress": buyer_address,
            "buyerRegistrationType": buyer.get("buyerRegistrationType", "Unregistered"),
            "buyerSTRN": buyer.get("buyerSTRN", ""),
            "customFields": custom_fields,
            "signatureArea": signature_area,
        }

        # PDF-only, both of them: FBR's invoice schema has neither field, and
        # app.py strips them again on the way out. They are here so they are
        # stored with the invoice and the PDF can be re-rendered from the row.
        if sales_person:
            invoice_json["salesPerson"] = sales_person
        buyer_phone = _normalize_phone(buyer.get("buyerPhone")) if prints_buyer_phone else ""
        if buyer_phone:
            invoice_json["buyerPhone"] = buyer_phone
        # Copied onto the invoice rather than looked up at render time, so a
        # reprint reproduces the sheet as it was sent even if the buyer's
        # vendor number or delivery plant is changed afterwards.
        if prints_buyer_supplier_fields:
            supplier_number = _normalize_supplier_number(buyer.get("buyerSupplierNumber"))
            if supplier_number:
                invoice_json["buyerSupplierNumber"] = supplier_number
            delivery_address = _normalize_delivery_address(buyer.get("buyerDeliveryAddress"))
            if delivery_address:
                invoice_json["buyerDeliveryAddress"] = delivery_address
        # Stored whatever the buyer's registration type is: whether the line
        # actually prints is decided at render time, so changing a buyer from
        # unregistered to registered later still prints the rate they chose.
        if advance_tax_percent is not None:
            invoice_json["advanceTaxPercent"] = float(advance_tax_percent)

        # Printed-only, both of them: FBR's invoice schema has neither, and
        # app.py strips them again before the call. Stored with the invoice
        # so a reprint from the Download Center reproduces the same sheet.
        if prints_reference_fields:
            reference = sanitize_string(data.get("reference"))[:120]
            if reference:
                invoice_json["reference"] = reference
            term_of_sales = sanitize_string(data.get("termOfSales"))[:120]
            if term_of_sales:
                invoice_json["termOfSales"] = term_of_sales

        if username == "8974121" and data.get("CNIC"):
            invoice_json["CNIC"] = data["CNIC"]
        if data.get("invoiceRefNo"):
            invoice_json["invoiceRefNo"] = data["invoiceRefNo"]
        if data.get("poNumber"):
            invoice_json["PO"] = data["poNumber"]
            invoice_json["poNumber"] = data["poNumber"]
        if data.get("dnNumber"):
            invoice_json["DN"] = data["dnNumber"]
            invoice_json["dnNumber"] = data["dnNumber"]
        if env == "sandbox" and data.get("scenarioId"):
            invoice_json["scenarioId"] = data.get("scenarioId")

        # Items
        items_list = []

        # Helper to quantize monetary values to 2 decimals with half-up rounding
        def q2(val):
            return float(Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        for item_data in data["items"]:
            try:
                value_excl = q2(item_data["valueSalesExcludingST"])
                sales_tax = q2(item_data["salesTaxApplicable"])

                # Handle taxRate consistently
                tax_rate = item_data.get("taxRate", "0%")
                # Ensure it ends with % if it's a numeric string without %
                if isinstance(tax_rate, str) and not tax_rate.endswith('%') and tax_rate.replace('.', '', 1).isdigit():
                    tax_rate = f"{tax_rate}%"

                # Re-derive the tax from the value and rate we are about to send,
                # using exact decimal half-up rounding. If the client's figure is
                # only a rounding artifact away, snap to what FBR will compute so
                # the invoice is not rejected over a single paisa. Anything
                # further out is a different tax basis and is left untouched.
                recomputed_tax = expected_sales_tax(value_excl, tax_rate)
                if recomputed_tax is not None and recomputed_tax > 0:
                    drift = abs(Decimal(str(sales_tax)) - recomputed_tax)
                    if 0 < drift <= SALES_TAX_SNAP_TOLERANCE:
                        print(
                            f"[Invoice] Sales tax adjusted {sales_tax} -> {recomputed_tax} "
                            f"(value {value_excl} @ {tax_rate}) to match FBR's calculation"
                        )
                        sales_tax = float(recomputed_tax)

                total_values = item_data.get("totalValues")
                # Ensure the computed sum is also rounded to 2 decimals to avoid float precision artifacts
                if total_values is None:
                    total_values = q2(value_excl + sales_tax)
                else:
                    total_values = q2(total_values)
                    # Keep the gross consistent if it was just value + tax and the
                    # tax moved above.
                    gross = q2(value_excl + sales_tax)
                    if abs(Decimal(str(total_values)) - Decimal(str(gross))) <= SALES_TAX_SNAP_TOLERANCE:
                        total_values = gross


                item = {
                    "hsCode": sanitize_string(item_data.get("hsCode", "")),
                    "productDescription": sanitize_string(item_data["productDescription"]),
                    "quantity": float(item_data["quantity"]),
                    "uoM": sanitize_string(item_data.get("uoM", "")),
                    "totalValues": total_values,
                    "valueSalesExcludingST": value_excl,
                    "salesTaxApplicable": sales_tax,
                    "rate": tax_rate,
                }
                # Defaults
                defaults = {
                    "fixedNotifiedValueOrRetailPrice": 0,
                    "salesTaxWithheldAtSource": 0,
                    "extraTax": "",
                    "furtherTax": 0,
                    "sroScheduleNo": "",
                    "fedPayable": 0,
                    "discount": 0,
                    "saleType": "Goods at standard rate (default)",
                    "sroItemSerialNo": "",
                }
                for f, default in defaults.items():
                    if f in item_data and item_data[f] is not None:
                        if f in [
                            "fixedNotifiedValueOrRetailPrice",
                            "salesTaxWithheldAtSource",
                            "furtherTax",
                            "fedPayable",
                            "discount",
                        ]:
                            item[f] = float(item_data[f])
                        else:
                            item[f] = str(item_data[f])
                    else:
                        item[f] = default

                # Fixed for this client, on every line, whatever the form
                # sent. Applied after the defaults loop above, which is what
                # would otherwise copy sroScheduleNo straight from the payload.
                if fixed_sro_297:
                    item["sroScheduleNo"] = SRO_297_SCHEDULE
                    item["sroItemSerialNo"] = SRO_297_ITEM_SERIAL
                    item["saleType"] = SRO_297_SALE_TYPE

                if is_special_user:
                    item["hs_code"] = item_data.get("hs_code") or item_data.get("hsCode") or ""
                    item["product_code"] = item_data.get("product_code") or item_data.get("productCode") or ""
                # FBR's own words for the HS code, read off the same
                # /pdi/v1/itemdesccode response the code was picked from.
                # Printed beside it on one client's sheet; nothing is derived
                # from it and FBR is not told it back.
                if prints_hs_description:
                    hs_description = sanitize_string(
                        item_data.get("hsCodeDescription")
                        or item_data.get("hs_code_description")
                    )
                    # The form sends it with the line, but only when the code
                    # was picked from the autocomplete in this session. A code
                    # that was typed, or one that came back on a saved product
                    # or a draft, arrives bare and used to print an empty
                    # cell. Look it up here instead of trusting the browser to
                    # have carried it: the column is FBR's text for a code
                    # that is already on the line, so the server can always
                    # answer it.
                    if not hs_description:
                        hs_description = sanitize_string(
                            fetch_hs_code_description(
                                item.get("hsCode"), get_db_connection, get_env))
                    item["hsCodeDescription"] = hs_description[:200]
                # The manufacturer's part number, printed in front of the
                # description in their Product Code & Description column.
                if types_own_product_code:
                    item["productCode"] = sanitize_string(
                        item_data.get("productCode") or item_data.get("product_code")
                    )[:60]
                # Their Part Unit column, where it is a figure of its own.
                # Sent only when it has one: a blank box leaves the field off
                # the line entirely, and the template falls back to the
                # derivation it has always used rather than printing a zero.
                if types_part_unit:
                    part_unit = _normalize_part_unit(item_data.get("partUnit"))
                    if part_unit is not None:
                        item["partUnit"] = part_unit
                        # Their money is Part Unit x Rate, not Quantity x
                        # Rate -- the quantity is a weight or a pack count
                        # and is filed as the quantity, nothing more. So the
                        # rate has to travel with the line: app.py derives
                        # the Unit Rate column as value / quantity when a
                        # line does not carry one, which is the same figure
                        # for everyone else and the wrong one here.
                        unit_rate = _normalize_part_unit(item_data.get("unitrate"))
                        if unit_rate is not None:
                            item["unitrate"] = unit_rate
                # Printed beside the quantity on the paper clients' replicas.
                # Only theirs carry the column, so only their payload carries
                # the field; app.py strips it again before FBR sees it.
                if wants_reams:
                    item["numberOfReams"] = _normalize_reams(item_data.get("numberOfReams"))
                items_list.append(item)

                # Persist product if new (reactivate if soft deleted)
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, is_active FROM products
                    WHERE client_id = %s AND LOWER(description) = LOWER(%s)
                    """,
                    (client_id, item_data["productDescription"]),
                )
                existing = cur.fetchone()
                if existing:
                    if existing[1] is False:
                        cur.execute(
                            "UPDATE products SET is_active = TRUE WHERE id = %s",
                            (existing[0],),
                        )
                        conn.commit()
                else:
                    # Process tax rate before executing query
                    tax_rate_str = item_data.get("taxRate", "17%")
                    tax_rate_num = tax_rate_str.replace("%", "") if isinstance(tax_rate_str, str) else tax_rate_str
                    # Try to convert to float, fallback to 17 if it fails
                    try:
                        float(tax_rate_num)
                    except (ValueError, TypeError):
                        tax_rate_num = "17"
                        
                    # The clients who type their own codes keep the one they
                    # typed on the line, so the next invoice fills it in for
                    # them. Everybody else's code is assigned on the products
                    # page and is not this route's business. Guarded on the
                    # column existing so a deploy ahead of the migration
                    # still saves the product.
                    columns = ["client_id", "description", "hs_code", "uom",
                               "default_tax_rate", "sale_type"]
                    values = [
                        client_id,
                        item_data["productDescription"],
                        item_data.get("hsCode", ""),
                        item_data.get("uoM", "Numbers, pieces, units"),
                        tax_rate_num,
                        item_data.get("saleType", "Goods at Reduced Rate"),
                    ]
                    line_code = (item.get("productCode") or "").strip()
                    if (types_own_product_code and line_code
                            and master_data.product_code_column_exists(cur)):
                        columns.append("product_code")
                        values.append(line_code)
                    cur.execute(
                        f"""
                        INSERT INTO products ({', '.join(columns)}, is_active)
                        VALUES ({', '.join(['%s'] * len(values))}, TRUE)
                        """,
                        values,
                    )
                    conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                return jsonify({"error": f"Error processing item: {str(e)}"}), 400

        invoice_json["items"] = items_list
        invoice_json["client_id"] = client_id

        # Store into global cache (used by PDF generator)
        import app  # local import to avoid circular import at module load time
        app.last_json_data[env] = invoice_json

        # Draft save
        if data.get("saveDraft"):
            conn = get_db_connection()
            cur = conn.cursor()
            draft_title = data.get("title", "")
            original_env = data.get("original_env", env)
            complete_invoice_data = {
                "invoiceType": data["invoiceType"],
                "invoiceDate": data["invoiceDate"],
                "invoiceRefNo": data.get("invoiceRefNo", ""),
                "scenarioId": data.get("scenarioId", ""),
                "poNumber": data.get("poNumber", ""),
                "PO": data.get("poNumber", ""),
                "dnNumber": data.get("dnNumber", ""),
                "reference": invoice_json.get("reference", ""),
                "termOfSales": invoice_json.get("termOfSales", ""),
                "sellerData": seller,
                "buyerData": buyer,
                "items": data["items"],
                "customFields": custom_fields,
                "signatureArea": signature_area,
                "salesPerson": sales_person,
                "advanceTaxPercent": (
                    float(advance_tax_percent) if advance_tax_percent is not None else None
                ),
                "client_id": client_id,
                "created_env": env,
                "totalAmount": sum(i["totalValues"] for i in items_list),
            }

            if data.get("draft_id"):
                cur.execute(
                    """
                    SELECT id FROM invoice_drafts
                    WHERE id = %s AND client_id = %s
                    """,
                    (data["draft_id"], client_id),
                )
                if cur.fetchone():
                    cur.execute(
                        """
                        UPDATE invoice_drafts
                        SET invoice_data = %s,
                            seller_profile_id = %s,
                            buyer_id = %s,
                            status = 'draft',
                            updated_at = NOW(),
                            title = %s,
                            last_accessed = NOW(),
                            env = %s,
                            original_env = %s
                        WHERE id = %s AND client_id = %s
                        RETURNING id
                        """,
                        (
                            json.dumps(complete_invoice_data),
                            seller.get("id"),
                            buyer.get("id"),
                            draft_title,
                            env,
                            original_env,
                            data["draft_id"],
                            client_id,
                        ),
                    )
                    draft_id = cur.fetchone()[0]
                else:
                    cur.close()
                    conn.close()
                    return jsonify({"error": "Draft not found or access denied"}), 404
            else:
                cur.execute(
                    """
                    INSERT INTO invoice_drafts
                      (client_id, env, original_env, seller_profile_id, buyer_id,
                       invoice_data, status, title, last_accessed)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                    RETURNING id
                    """,
                    (
                        client_id,
                        env,
                        original_env,
                        seller.get("id"),
                        buyer.get("id"),
                        json.dumps(complete_invoice_data),
                        "draft",
                        draft_title,
                    ),
                )
                draft_id = cur.fetchone()[0]

            conn.commit()
            cur.close()
            conn.close()
            return jsonify(
                {
                    "message": "Invoice saved as draft",
                    "draft_id": draft_id,
                    "title": draft_title,
                    "invoice_json": invoice_json,
                }
            )

        # Submit directly
        if data.get("submit"):
            from app import submit_fbr  # local import
            return submit_fbr()

        return jsonify(
            {
                "message": "Invoice JSON created successfully",
                "invoice_json": invoice_json,
            }
        )
