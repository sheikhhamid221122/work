"""
API routes to support the form-based invoice creation
"""
from flask import request, jsonify, session
import json
from datetime import datetime


# Business Profiles API
def add_invoice_form_routes(app, get_db_connection, get_env):
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

        profiles = []
        for row in cur.fetchall():
            profiles.append(
                {
                    "id": row[0],
                    "business_name": row[1],
                    "address": row[2],
                    "province": row[3],
                    "ntn_cnic": row[4],
                    "strn": row[5],
                    "is_default": row[6],
                }
            )

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

        # If this is marked as default, unset any existing defaults
        if data.get("is_default", False):
            cur.execute(
                """
                UPDATE business_profiles 
                SET is_default = FALSE 
                WHERE client_id = %s
            """,
                (client_id,),
            )

        # Insert new profile
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

        return jsonify(
            {"id": new_id, "message": "Business profile created successfully"}
        )

    @app.route("/api/business-profiles/<int:profile_id>", methods=["PUT"])
    def update_business_profile(profile_id):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json()
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify ownership
        cur.execute(
            """
            SELECT id FROM business_profiles 
            WHERE id = %s AND client_id = %s
        """,
            (profile_id, client_id),
        )

        if not cur.fetchone():
            cur.close()
            conn.close()
            return (
                jsonify({"error": "Business profile not found or access denied"}),
                404,
            )

        # If this is marked as default, unset any existing defaults
        if data.get("is_default", False):
            cur.execute(
                """
                UPDATE business_profiles 
                SET is_default = FALSE 
                WHERE client_id = %s
            """,
                (client_id,),
            )

        # Update profile
        fields = [
            "business_name",
            "address",
            "province",
            "ntn_cnic",
            "strn",
            "is_default",
        ]
        updates = [f"{field} = %s" for field in fields if field in data]
        values = [data[field] for field in fields if field in data]

        if updates:
            query = f"""
                UPDATE business_profiles 
                SET {', '.join(updates)}, updated_at = NOW() 
                WHERE id = %s AND client_id = %s
            """
            values.append(profile_id)
            values.append(client_id)
            cur.execute(query, values)

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"message": "Business profile updated successfully"})

    # Buyers API
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

        buyers = []
        for row in cur.fetchall():
            buyers.append(
                {
                    "id": row[0],
                    "business_name": row[1],
                    "address": row[2],
                    "province": row[3],
                    "ntn_cnic": row[4],
                    "strn": row[5],
                    "registration_type": row[6],
                    "buyer_code": row[7],
                    "is_default": row[8],
                }
            )

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

        # If this is marked as default, unset any existing defaults
        if data.get("is_default", False):
            cur.execute(
                """
                UPDATE buyers 
                SET is_default = FALSE 
                WHERE client_id = %s
            """,
                (client_id,),
            )

        # Insert new buyer
        cur.execute(
            """
            INSERT INTO buyers 
            (client_id, business_name, address, province, ntn_cnic, strn, registration_type, buyer_code, is_default) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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

    # Products API - Enhanced for client-specific products
    @app.route("/api/products", methods=["GET"])
    def get_products():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        conn = get_db_connection()
        cur = conn.cursor()

        # First get the username for the client
        cur.execute(
            """
            SELECT u.username 
            FROM users u
            JOIN clients c ON u.id = c.user_id
            WHERE c.id = %s
        """,
            (client_id,),
        )

        user_row = cur.fetchone()
        if not user_row:
            cur.close()
            conn.close()
            return jsonify({"error": "User not found"}), 404

        username = user_row[0]

        # Get products based on client ID
        cur.execute(
            """
            SELECT id, description, hs_code, rate, uom, default_tax_rate, sro_schedule_no, sale_type 
            FROM products 
            WHERE client_id = %s 
            ORDER BY description
        """,
            (client_id,),
        )

        products = []
        for row in cur.fetchall():
            products.append(
                {
                    "id": row[0],
                    "description": row[1],
                    "hs_code": row[2],
                    "rate": float(row[3]) if row[3] else 0,
                    "uom": row[4],
                    "default_tax_rate": float(row[5]) if row[5] else 0,
                    "sro_schedule_no": row[6],
                    "sale_type": row[7],
                }
            )

        cur.close()
        conn.close()
        return jsonify(products)

    @app.route("/api/products", methods=["POST"])
    def create_product():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json()

        # Validate required fields
        if "description" not in data or not data["description"]:
            return jsonify({"error": "Product description is required"}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Check if product with same description already exists to avoid duplicates
        cur.execute(
            """
            SELECT id FROM products 
            WHERE client_id = %s AND description = %s
        """,
            (client_id, data["description"]),
        )

        existing = cur.fetchone()
        if existing:
            cur.close()
            conn.close()
            return (
                jsonify({"id": existing[0], "message": "Product already exists"}),
                200,
            )

        # Insert new product
        cur.execute(
            """
            INSERT INTO products 
            (client_id, description, hs_code, rate, uom, default_tax_rate, sro_schedule_no, sale_type) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """,
            (
                client_id,
                data["description"],
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

    # New endpoint to get user-specific product settings
    @app.route("/api/user-product-settings", methods=["GET"])
    def get_user_product_settings():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        conn = get_db_connection()
        cur = conn.cursor()

        # Get username for the client
        cur.execute(
            """
            SELECT u.username 
            FROM users u
            JOIN clients c ON u.id = c.user_id
            WHERE c.id = %s
        """,
            (client_id,),
        )

        user_row = cur.fetchone()
        if not user_row:
            cur.close()
            conn.close()
            return jsonify({"error": "User not found"}), 404

        username = user_row[0]

        # Define which usernames should use product dropdown
        # This can be moved to a database table later for better flexibility
        product_dropdown_users = ["3075270"]  # Add more usernames as needed

        settings = {
            "useProductDropdown": username in product_dropdown_users,
            "username": username,
        }

        cur.close()
        conn.close()
        return jsonify(settings)

    # Helper endpoint to get form dropdown options
    # Helper endpoint to get form dropdown options
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
                    {
                        "value": "Numbers, pieces, units",
                        "label": "Numbers, pieces, units",
                    },
                    {"value": "KG", "label": "KG - Kilogram"},
                    {"value": "MT", "label": "MT - Metric Ton"},
                    {"value": "LTR", "label": "LTR - Liter"},
                    {"value": "KWH", "label": "KWH - Kilowatt Hour"},
                    {"value": "MTR", "label": "MTR - Meter"},
                    # You can keep the rest of the UOMs if needed or remove them
                ],
                "scenarioIds": [
                    {"value": "SN001", "label": "SN001"},
                    {"value": "SN002", "label": "SN002"},
                    {"value": "SN003", "label": "SN003"},
                    {"value": "SN004", "label": "SN004"},
                    {"value": "SN005", "label": "SN005"},
                    {"value": "SN006", "label": "SN006"},
                    {"value": "SN007", "label": "SN007"},
                    {"value": "SN008", "label": "SN008"},
                    {"value": "SN009", "label": "SN009"},
                    {"value": "SN010", "label": "SN010"},
                    {"value": "SN011", "label": "SN011"},
                    {"value": "SN012", "label": "SN012"},
                    {"value": "SN013", "label": "SN013"},
                    {"value": "SN014", "label": "SN014"},
                    {"value": "SN015", "label": "SN015"},
                    {"value": "SN016", "label": "SN016"},
                    {"value": "SN017", "label": "SN017"},
                    {"value": "SN018", "label": "SN018"},
                    {"value": "SN019", "label": "SN019"},
                    {"value": "SN020", "label": "SN020"},
                    {"value": "SN021", "label": "SN021"},
                    {"value": "SN022", "label": "SN022"},
                    {"value": "SN023", "label": "SN023"},
                    {"value": "SN024", "label": "SN024"},
                    {"value": "SN025", "label": "SN025"},
                    {"value": "SN026", "label": "SN026"},
                    {"value": "SN027", "label": "SN027"},
                    {"value": "SN028", "label": "SN028"},
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

    # Batch import endpoint for user-specific products
    # Batch import endpoint for user-specific products

    @app.route("/api/products/batch-import", methods=["POST"])
    def batch_import_products():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        # Get username for the client
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

        user_row = cur.fetchone()
        username = user_row[0] if user_row else None

        data = request.get_json()
        product_list = data.get("products", [])

        if not product_list:
            return jsonify({"error": "No products provided for import"}), 400

        results = {"imported": 0, "skipped": 0}

        # Set SRO values based on username
        sro_schedule_no = "EIGHTH SCHEDULE Table 1" if username == "3075270" else ""
        sro_item_serial_no = "81" if username == "3075270" else ""

        for product_name in product_list:
            # Check if product already exists
            cur.execute(
                """
                SELECT id FROM products 
                WHERE client_id = %s AND description = %s
            """,
                (client_id, product_name),
            )

            if cur.fetchone():
                results["skipped"] += 1
                continue

            # Insert new product with appropriate SRO values
            cur.execute(
                """
                INSERT INTO products 
                (client_id, description, hs_code, uom, default_tax_rate, sale_type, sro_schedule_no, sro_item_serial_no) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    client_id,
                    product_name,
                    "",  # hs_code
                    "Numbers, pieces, units",  # uom
                    1,  # default_tax_rate (1% instead of 17%)
                    "Goods at Reduced Rate",  # sale_type
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

    # Invoice Creation API
    # Invoice Creation API
    @app.route("/api/invoice/create", methods=["POST"])
    def create_invoice_from_form():
        client_id = session.get("client_id")
        env = get_env()

        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json()

        # Validate core invoice data
        required_fields = [
            "invoiceType",
            "invoiceDate",
            "sellerData",
            "buyerData",
            "items",
        ]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # Validate seller data
        seller = data["sellerData"]
        seller_required_fields = [
            "sellerBusinessName",
            "sellerAddress",
            "sellerProvince",
            "sellerNTNCNIC",
        ]
        for field in seller_required_fields:
            if field not in seller:
                return (
                    jsonify({"error": f"Missing required seller field: {field}"}),
                    400,
                )

        # Validate buyer data
        buyer = data["buyerData"]
        buyer_required_fields = [
            "buyerBusinessName",
            "buyerAddress",
            "buyerProvince",
            "buyerNTNCNIC",
        ]
        for field in buyer_required_fields:
            if field not in buyer:
                return jsonify({"error": f"Missing required buyer field: {field}"}), 400

        # Validate items
        if not data["items"] or len(data["items"]) == 0:
            return jsonify({"error": "At least one item is required"}), 400

        # Construct invoice JSON
        invoice_json = {
            "invoiceType": data["invoiceType"],
            "invoiceDate": data["invoiceDate"],
            "sellerNTNCNIC": seller["sellerNTNCNIC"],
            "sellerBusinessName": seller["sellerBusinessName"],
            "sellerProvince": seller["sellerProvince"],
            "sellerAddress": seller["sellerAddress"],
            "buyerNTNCNIC": buyer["buyerNTNCNIC"],
            "buyerBusinessName": buyer["buyerBusinessName"],
            "buyerProvince": buyer["buyerProvince"],
            "buyerAddress": buyer["buyerAddress"],
            "buyerRegistrationType": buyer.get("buyerRegistrationType", "Unregistered"),
        }

        # Add optional fields
        if "invoiceRefNo" in data:
            invoice_json["invoiceRefNo"] = data["invoiceRefNo"]

        # Add PO number to invoice_json if it exists
        if "poNumber" in data and data["poNumber"]:
            invoice_json["PO"] = data["poNumber"]

        # Add scenario ID only to sandbox environment
        if env == "sandbox" and "scenarioId" in data:
            invoice_json["scenarioId"] = data["scenarioId"]

        # Add items with proper format required by FBR
        items_list = []
        for item_data in data["items"]:
            try:
                # Make sure valueSalesExcludingST and salesTaxApplicable are numbers
                value_excl = round(float(item_data["valueSalesExcludingST"]), 2)
                sales_tax = round(float(item_data["salesTaxApplicable"]), 2)

                # Calculate totalValues if not provided
                total_values = item_data.get("totalValues")
                if total_values is None:
                    total_values = value_excl + sales_tax
                else:
                    total_values = round(float(total_values), 2)

                # Required item fields
                item = {
                    "hsCode": item_data.get("hsCode", ""),
                    "productDescription": item_data["productDescription"],
                    "quantity": float(item_data["quantity"]),
                    "uoM": item_data.get("uoM", ""),
                    "totalValues": total_values,
                    "valueSalesExcludingST": value_excl,
                    "salesTaxApplicable": sales_tax,
                }

                # Make sure to use taxRate as the rate (with percentage symbol)
                if "taxRate" in item_data:
                    item["rate"] = item_data["taxRate"]
                else:
                    # Default to 0%
                    item["rate"] = "0%"

                # Add all required FBR fields with defaults if not provided
                item_fields = {
                    "fixedNotifiedValueOrRetailPrice": 0,
                    "salesTaxWithheldAtSource": 0,
                    "extraTax": "",
                    "furtherTax": 0,
                    "sroScheduleNo": "",
                    "fedPayable": 0,
                    "discount": 0,
                    "saleType": "Goods at Reduced Rate",
                    "sroItemSerialNo": "S1",
                }

                # Override defaults with any provided values
                for field, default in item_fields.items():
                    if field in item_data and item_data[field] is not None:
                        if field in [
                            "fixedNotifiedValueOrRetailPrice",
                            "salesTaxWithheldAtSource",
                            "furtherTax",
                            "fedPayable",
                            "discount",
                        ]:
                            item[field] = float(item_data[field])
                        else:
                            item[field] = str(item_data[field])
                    else:
                        item[field] = default

                items_list.append(item)

                # Automatically save new products when they are used
                # This helps build the product catalog over time
                conn = get_db_connection()
                cur = conn.cursor()

                # Check if product already exists
                cur.execute(
                    """
                    SELECT id FROM products 
                    WHERE client_id = %s AND description = %s
                """,
                    (client_id, item_data["productDescription"]),
                )

                if not cur.fetchone():
                    # Insert new product
                    cur.execute(
                        """
                        INSERT INTO products 
                        (client_id, description, hs_code, uom, default_tax_rate, sale_type) 
                        VALUES (%s, %s, %s, %s, %s, %s)
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
        
        # Add client_id for data isolation
        invoice_json["client_id"] = client_id

        # Directly store the JSON in app's global space to avoid import problems
        import app
        app.last_json_data[env] = invoice_json

        # Store as draft if requested
        if data.get("saveDraft", False):
            conn = get_db_connection()
            cur = conn.cursor()

            # Get draft title from request or use a default
            draft_title = data.get("title", "")
            
            # Track the original environment where draft was created
            original_env = data.get("original_env", env)

            # Prepare complete invoice data for storage
            # This will include all form data needed to reconstruct the entire form
            complete_invoice_data = {
                # FBR submission data
                "invoiceType": data["invoiceType"],
                "invoiceDate": data["invoiceDate"],
                "invoiceRefNo": data.get("invoiceRefNo", ""),
                "scenarioId": data.get("scenarioId", ""),
                "poNumber": data.get("poNumber", ""),
                "PO": data.get("poNumber", ""),
                
                # Form data structures
                "sellerData": seller,
                "buyerData": buyer,
                "items": data["items"],
                
                # Additional metadata
                "client_id": client_id,
                "created_env": env,
                "totalAmount": sum(item["totalValues"] for item in items_list)
            }

            # Check if updating an existing draft or creating a new one
            if "draft_id" in data and data["draft_id"]:
                # Update existing draft - verify ownership first
                cur.execute(
                    """
                    SELECT id FROM invoice_drafts 
                    WHERE id = %s AND client_id = %s
                    """,
                    (data["draft_id"], client_id),
                )
                
                if cur.fetchone():
                    # Update draft
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
                    return jsonify({"error": "Draft not found or access denied"}), 404
            else:
                # Insert new draft
                cur.execute(
                    """
                    INSERT INTO invoice_drafts
                    (client_id, env, original_env, seller_profile_id, buyer_id, 
                    invoice_data, status, title, last_accessed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
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

        # If submit is requested, call submit_fbr directly rather than importing
        if data.get("submit", False):
            # Use Flask's current_app to avoid circular imports
            from flask import current_app

            # Call the submit_fbr function but pass along our json data
            # This works around the circular import issue
            from app import submit_fbr

            return submit_fbr()

        return jsonify(
            {
                "message": "Invoice JSON created successfully",
                "invoice_json": invoice_json,
            }
        )


