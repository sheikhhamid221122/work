"""
API routes to support the form-based invoice creation
"""
from flask import request, jsonify, session
import json
from datetime import datetime


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
        data = request.get_json()

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

    # ---------------- Products (with soft delete) ----------------
    @app.route("/api/products", methods=["GET"])
    def get_products():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        conn = get_db_connection()
        cur = conn.cursor()
        # (username fetch retained only if needed later)
        cur.execute(
            """
            SELECT u.username 
            FROM users u
            JOIN clients c ON u.id = c.user_id
            WHERE c.id = %s
            """,
            (client_id,),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": "User not found"}), 404

        # Only active (legacy rows with NULL still show)
        cur.execute(
            """
            SELECT id, description, hs_code, rate, uom, default_tax_rate,
                   sro_schedule_no, sale_type
            FROM products
            WHERE client_id = %s
              AND (is_active = TRUE OR is_active IS NULL)
            ORDER BY description
            """,
            (client_id,),
        )
        products = [
            {
                "id": r[0],
                "description": r[1],
                "hs_code": r[2],
                "rate": float(r[3]) if r[3] else 0,
                "uom": r[4],
                "default_tax_rate": float(r[5]) if r[5] else 0,
                "sro_schedule_no": r[6],
                "sale_type": r[7],
            }
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify(products)

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
        # Case-insensitive match
        cur.execute(
            """
            SELECT id, is_active FROM products
            WHERE client_id = %s AND LOWER(description) = LOWER(%s)
            """,
            (client_id, description),
        )
        existing = cur.fetchone()
        if existing:
            if existing[1] is False:  # Reactivate soft-deleted
                cur.execute(
                    "UPDATE products SET is_active = TRUE WHERE id = %s",
                    (existing[0],),
                )
                conn.commit()
            cur.close()
            conn.close()
            return jsonify({"id": existing[0], "message": "Product already exists"}), 200

        cur.execute(
            """
            INSERT INTO products
              (client_id, description, hs_code, rate, uom,
               default_tax_rate, sro_schedule_no, sale_type, is_active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s, TRUE)
            RETURNING id
            """,
            (
                client_id,
                description,
                data.get("hs_code", ""),
                data.get("rate", 0),
                data.get("uom", ""),
                data.get("default_tax_rate", 0),
                data.get("sro_schedule_no", ""),
                data.get("sale_type", ""),
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"id": new_id, "message": "Product created successfully"})

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
        return jsonify(
            {
                "invoiceTypes": [
                    {"value": "Sale Invoice", "label": "Sale Invoice"},
                    {"value": "Credit Note", "label": "Credit Note"},
                    {"value": "Debit Note", "label": "Debit Note"},
                ],
                "provinces": [
                    {"value": "Punjab", "label": "Punjab"},
                    {"value": "Sindh", "label": "Sindh"},
                    {"value": "KPK", "label": "KPK"},
                    {"value": "Balochistan", "label": "Balochistan"},
                    {"value": "Gilgit-Baltistan", "label": "Gilgit-Baltistan"},
                    {
                        "value": "Azad Jammu and Kashmir",
                        "label": "Azad Jammu and Kashmir",
                    },
                    {
                        "value": "Islamabad Capital Territory",
                        "label": "Islamabad Capital Territory",
                    },
                ],
                "registrationTypes": [
                    {"value": "Registered", "label": "Registered"},
                    {"value": "Unregistered", "label": "Unregistered"},
                    {"value": "NTN Tax Base", "label": "NTN Tax Base"},
                ],
                "uoms": [
                    {"value": "Numbers, pieces, units", "label": "Numbers, pieces, units"},
                    {"value": "KG", "label": "KG - Kilogram"},
                    {"value": "MT", "label": "MT - Metric Ton"},
                    {"value": "LTR", "label": "LTR - Liter"},
                    {"value": "KWH", "label": "KWH - Kilowatt Hour"},
                    {"value": "MTR", "label": "MTR - Meter"},
                ],
                "scenarioIds": [
                    {"value": f"SN{str(i).zfill(3)}", "label": f"SN{str(i).zfill(3)}"}
                    for i in range(1, 29)
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
        cur.close()
        conn.close()
        
        seller_address = seller["sellerAddress"].strip().replace("\n", " ")
        buyer_address = buyer["buyerAddress"].strip().replace("\n", " ")

        invoice_json = {
            "invoiceType": data["invoiceType"],
            "invoiceDate": data["invoiceDate"],
            "sellerNTNCNIC": seller["sellerNTNCNIC"],
            "sellerBusinessName": seller["sellerBusinessName"],
            "sellerProvince": seller["sellerProvince"],
            "sellerAddress": seller_address,
            "buyerNTNCNIC": buyer["buyerNTNCNIC"],
            "buyerBusinessName": buyer["buyerBusinessName"],
            "buyerProvince": buyer["buyerProvince"],
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
        if env == "sandbox" and data.get("scenarioId"):
            invoice_json["scenarioId"] = data.get("scenarioId")

        # Items
        items_list = []
        for item_data in data["items"]:
            try:
                value_excl = round(float(item_data["valueSalesExcludingST"]), 2)
                sales_tax = round(float(item_data["salesTaxApplicable"]), 2)
                total_values = item_data.get("totalValues")
                total_values = (
                    value_excl + sales_tax
                    if total_values is None
                    else round(float(total_values), 2)
                )
                item = {
                    "hsCode": item_data.get("hsCode", ""),
                    "productDescription": item_data["productDescription"],
                    "quantity": float(item_data["quantity"]),
                    "uoM": item_data.get("uoM", ""),
                    "totalValues": total_values,
                    "valueSalesExcludingST": value_excl,
                    "salesTaxApplicable": sales_tax,
                    "rate": item_data.get("taxRate", "0%"),
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
                    "saleType": "Goods at Reduced Rate",
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
                            item_data.get("taxRate", "17%").replace("%", ""),
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
                "sellerData": seller,
                "buyerData": buyer,
                "items": data["items"],
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