"""
API routes to support the form-based invoice creation
"""
from flask import request, jsonify, session
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

SPECIAL_USERNAMES = {"H075895", "F667833", "infinityeng"}


def _is_special_username(username):
    return (username or "").strip() in SPECIAL_USERNAMES


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
        cur.execute(
            """
            SELECT id, business_name, address, province, ntn_cnic, strn, registration_type, buyer_code, is_default 
            FROM buyers 
            WHERE client_id = %s 
            ORDER BY is_default DESC, business_name
            """,
            (client_id,),
        )
        buyers = [
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
            }
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
        if data.get("is_default", False):
            cur.execute(
                "UPDATE buyers SET is_default = FALSE WHERE client_id = %s",
                (client_id,),
            )
        cur.execute(
            """
            INSERT INTO buyers
              (client_id, business_name, address, province, ntn_cnic, strn,
               registration_type, buyer_code, is_default)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                data.get("buyer_code", ""),
                data.get("is_default", False),
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"id": new_id, "message": "Buyer created successfully"})

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
            updates = []
            values = []
            for field in fields:
                if field in data:
                    updates.append(f"{field} = %s")
                    values.append(data[field] if data[field] is not None else "")
            
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

            should_include_product_code = has_product_code and is_special_user

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

            if has_product_code and is_special_user:
                columns.insert(3, "product_code")
                values.insert(3, (data.get("product_code") or "").strip())

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
            return jsonify({"id": new_id, "message": "Product created successfully"})
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
                product_code_value = (data.get("product_code") or "").strip()
                add_update("product_code", product_code_value if is_special_user else product_code_value)

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

    # ---------------- Form Options ----------------
    @app.route("/api/form-options", methods=["GET"])
    def get_form_options():
        # Static province list - matching values stored in database
        provinces_data = [
            {"value": "PUNJAB", "label": "PUNJAB"},
            {"value": "SINDH", "label": "SINDH"},
            {"value": "KPK", "label": "KPK"},
            {"value": "BALOCHISTAN", "label": "BALOCHISTAN"},
            {"value": "ISLAMABAD", "label": "ISLAMABAD"},
            {"value": "AJK", "label": "AJK"},
            {"value": "GILGIT BALTISTAN", "label": "GILGIT BALTISTAN"},
        ]
        
        # Sale types per FBR Documentation Section 9 - Scenarios for Sandbox Testing
        # These are the official sale type values that map to each scenario
        sale_types_data = [
            {"value": "Goods at Standard Rate (default)", "label": "Goods at Standard Rate (default)"},
            {"value": "Steel melting and re-rolling", "label": "Steel melting and re-rolling"},
            {"value": "Ship breaking", "label": "Ship breaking"},
            {"value": "Goods at Reduced Rate", "label": "Goods at Reduced Rate"},
            {"value": "Exempt Goods", "label": "Exempt Goods"},
            {"value": "Goods at zero-rate", "label": "Goods at zero-rate"},
            {"value": "3rd Schedule Goods", "label": "3rd Schedule Goods"},
            {"value": "Cotton Ginners", "label": "Cotton Ginners"},
            {"value": "Telecommunication services", "label": "Telecommunication services"},
            {"value": "Toll Manufacturing", "label": "Toll Manufacturing"},
            {"value": "Petroleum Products", "label": "Petroleum Products"},
            {"value": "Electricity Supply to Retailers", "label": "Electricity Supply to Retailers"},
            {"value": "Gas to CNG stations", "label": "Gas to CNG stations"},
            {"value": "Mobile Phones", "label": "Mobile Phones"},
            {"value": "Processing/ Conversion of Goods", "label": "Processing/ Conversion of Goods"},
            {"value": "Goods (FED in ST Mode)", "label": "Goods (FED in ST Mode)"},
            {"value": "Services (FED in ST Mode)", "label": "Services (FED in ST Mode)"},
            {"value": "Services", "label": "Services"},
            {"value": "Electric Vehicle", "label": "Electric Vehicle"},
            {"value": "Cement /Concrete Block", "label": "Cement /Concrete Block"},
            {"value": "Potassium Chlorate", "label": "Potassium Chlorate"},
            {"value": "CNG Sales", "label": "CNG Sales"},
            {"value": "Goods as per SRO.297(|)/2023", "label": "Goods as per SRO.297(|)/2023"},
            {"value": "Non-Adjustable Supplies", "label": "Non-Adjustable Supplies"},
        ]
        
        return jsonify(
            {
                "invoiceTypes": [
                    {"value": "Sale Invoice", "label": "Sale Invoice"},
                    {"value": "Credit Note", "label": "Credit Note"},
                    {"value": "Debit Note", "label": "Debit Note"},
                ],
                "provinces": provinces_data,
                # Registration types per FBR doc - only 2 valid values for buyerRegistrationType
                "registrationTypes": [
                    {"value": "Registered", "label": "Registered"},
                    {"value": "Unregistered", "label": "Unregistered"},
                ],
                "saleTypes": sale_types_data,
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
            SELECT u.username 
            FROM users u
            JOIN clients c ON u.id = c.user_id
            WHERE c.id = %s
            """,
            (client_id,),
        )
        row = cur.fetchone()
        username = row[0] if row else None
        is_special_user = _is_special_username(username)
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

        invoice_json = {
            "invoiceType": sanitize_string(data["invoiceType"]),
            "invoiceDate": data["invoiceDate"],
            "sellerNTNCNIC": seller["sellerNTNCNIC"],
            "sellerBusinessName": sanitize_string(seller["sellerBusinessName"]),
            "sellerProvince": sanitize_string(seller["sellerProvince"]),
            "sellerAddress": seller_address,
            "sellerSTRN": seller.get("sellerSTRN", ""),
            "buyerNTNCNIC": buyer["buyerNTNCNIC"],
            "buyerBusinessName": sanitize_string(buyer["buyerBusinessName"]),
            "buyerProvince": sanitize_string(buyer["buyerProvince"]),
            "buyerAddress": buyer_address,
            "buyerRegistrationType": buyer.get("buyerRegistrationType", "Unregistered"),
            "buyerSTRN": buyer.get("buyerSTRN", ""),
        }

        if username == "8974121" and data.get("CNIC"):
            invoice_json["CNIC"] = data["CNIC"]
        if data.get("invoiceRefNo"):
            invoice_json["invoiceRefNo"] = data["invoiceRefNo"]
        if data.get("poNumber"):
            invoice_json["PO"] = data["poNumber"]
            invoice_json["poNumber"] = data["poNumber"]
        if is_special_user and ("DC" in data or "dcNumber" in data):
            invoice_json["DC"] = data.get("DC") or data.get("dcNumber") or ""
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
                total_values = item_data.get("totalValues")
                # Ensure the computed sum is also rounded to 2 decimals to avoid float precision artifacts
                total_values = (
                    q2(value_excl + sales_tax)
                    if total_values is None
                    else q2(total_values)
                )
                # Handle taxRate consistently
                tax_rate = item_data.get("taxRate", "0%")
                # Ensure it ends with % if it's a numeric string without %
                if isinstance(tax_rate, str) and not tax_rate.endswith('%') and tax_rate.replace('.', '', 1).isdigit():
                    tax_rate = f"{tax_rate}%"
                
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

                if is_special_user:
                    item["hs_code"] = item_data.get("hs_code") or item_data.get("hsCode") or ""
                    item["product_code"] = item_data.get("product_code") or item_data.get("productCode") or ""
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
                        
                    cur.execute(
                        """
                        INSERT INTO products
                          (client_id, description, hs_code, uom, default_tax_rate, sale_type, is_active)
                        VALUES (%s,%s,%s,%s,%s,%s, TRUE)
                        """,
                        (
                            client_id,
                            item_data["productDescription"],
                            item_data.get("hsCode", ""),
                            item_data.get("uoM", "Numbers, pieces, units"),
                            tax_rate_num,
                            item_data.get("saleType", "Goods at Reduced Rate"),
                        ),
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
                "DC": data.get("DC") or data.get("dcNumber") or "",
                "sellerData": seller,
                "buyerData": buyer,
                "items": data["items"],
                "client_id": client_id,
                "created_env": env,
                "totalAmount": sum(i["totalValues"] for i in items_list),
            }

            if not is_special_user and "DC" in complete_invoice_data:
                complete_invoice_data.pop("DC")

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