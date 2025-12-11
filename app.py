from reports_routes import add_reports_routes
from invoice_form_routes import add_invoice_form_routes
from draft_invoice_routes import add_draft_invoice_routes
from flask import Flask, render_template, request, jsonify, send_file
from flask import render_template
from flask import session, redirect, url_for
from collections import OrderedDict
import pandas as pd
import json
import os
import datetime
import requests
import qrcode
import tempfile
from weasyprint import HTML
import math
import base64
import psycopg2
from flask_cors import CORS
from dotenv import load_dotenv
from io import BytesIO

load_dotenv()
import datetime


app = Flask(__name__)
CORS(
    app,
    supports_credentials=True,
    origins=["https://erp-taxlinkpro.onrender.com", "http://127.0.0.1:5000"],
)
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


# Update session configuration
app.secret_key = os.getenv("SECRET_KEY", "myfallbacksecret")
print(app.secret_key)  # Debugging line to check secret key

app.config.update(
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=1),
    SESSION_COOKIE_NAME="erp_session",
    SESSION_COOKIE_PATH="/",
)


@app.route("/create-invoice.html")
def create_invoice_html():
    if "user_id" not in session:
        print("No user_id in session, redirecting to login")
        return redirect(url_for("index"))

    print("Access granted to create invoice page")
    return render_template("create-invoice.html")


@app.route("/api/generate-form-invoice", methods=["GET"])
def generate_form_invoice():
    env = get_env()
    client_id = session.get("client_id")
    user_id = session.get("user_id")

    if not client_id:
        return jsonify({"error": "No client ID in session"}), 401

    try:
        # IMPORTANT FIX: Always fetch fresh data for the current client
        # instead of relying on possibly stale data in last_json_data
        data = None
        conn = get_db_connection()
        cur = conn.cursor()

        # Get username for the client - we'll need this for template selection and special field handling
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
        username = str(user_row[0]).strip() if user_row and user_row[0] is not None else None

        print(f"Current username: {username}, client_id: {client_id}")  # Debugging log

        # Get the most recent invoice for this client and environment
        cur.execute(
            """
            SELECT invoice_data 
            FROM invoices 
            WHERE client_id = %s AND env = %s AND status = 'Success'
            ORDER BY created_at DESC
            LIMIT 1
        """,
            (client_id, env),
        )

        row = cur.fetchone()

        if row:
            # Convert stored JSON string to dictionary
            try:
                data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                print(f"Using database invoice data for client_id: {client_id}")
            except Exception as e:
                print(f"Error parsing invoice data from database: {str(e)}")

        # If no data from database, try using the session-specific data
        if not data and env in last_json_data:
            # IMPORTANT: Only use cached data if it belongs to current client
            if (
                "client_id" in last_json_data[env]
                and last_json_data[env]["client_id"] == client_id
            ):
                data = last_json_data[env].copy()
                print("Using in-memory invoice data for current client")
            else:
                print("Rejected stale in-memory data from different client")
                # Don't use data from a different client
                data = None

        # Step 3: If we still don't have data, return error
        if not data:
            return (
                jsonify(
                    {
                        "error": "No invoice data available. Please submit an invoice first."
                    }
                ),
                400,
            )

        # Ensure we store the client_id with the data for future reference
        data["client_id"] = client_id

        # Get client's STRN directly from clients table
        # First check if STRN is in the invoice_data directly
        if "sellerSTRN" in data and data["sellerSTRN"]:
            print(f"Using STRN from invoice data: {data['sellerSTRN']}")
        # Next check if it's in the nested sellerData structure (from form)
        elif "sellerData" in data and "sellerSTRN" in data["sellerData"]:
            data["sellerSTRN"] = data["sellerData"]["sellerSTRN"]
            print(f"Using STRN from form input: {data['sellerSTRN']}")
        # Fall back to client's database record only as a last resort
        else:
            cur.execute(
                "SELECT strn, logo_url FROM clients WHERE id = %s", (client_id,)
            )
            client_row = cur.fetchone()
            if client_row and client_row[0]:
                data["sellerSTRN"] = client_row[0]
                print(f"Using STRN from clients table: {data['sellerSTRN']}")
            else:
                # Try business_profiles as final fallback
                cur.execute(
                    """
                    SELECT strn FROM business_profiles 
                    WHERE client_id = %s AND is_default = true
                    LIMIT 1
                    """,
                    (client_id,),
                )
                bp_row = cur.fetchone()
                if bp_row and bp_row[0]:
                    data["sellerSTRN"] = bp_row[0]
                    print(f"Using STRN from business_profiles: {data['sellerSTRN']}")
                else:
                    data["sellerSTRN"] = ""

        client_logo_url = client_row[1] if client_row else None

        # Get FBR logo URL
        cur.execute("SELECT fbr_logo FROM fbr LIMIT 1;")
        fbr_row = cur.fetchone()
        fbr_logo_url = fbr_row[0] if fbr_row else None

                # Make sure PO# is available
        # Make sure PO# is available - replace the existing code block with this
        if "PO" not in data or not data["PO"]:
            print(f"Debug - Trying to find PO number in data structure")
            # First check if poNumber exists in the root of the data
            if "poNumber" in data:
                print(f"Debug - Found poNumber in root: {data['poNumber']}")
                data["PO"] = data["poNumber"]
            # Next check if it's in the invoiceData structure
            elif "invoiceData" in data and "poNumber" in data["invoiceData"]:
                print(f"Debug - Found poNumber in invoiceData: {data['invoiceData']['poNumber']}")
                data["PO"] = data["invoiceData"]["poNumber"]
            # Check if it's in complete_invoice_data if available
            elif "complete_invoice_data" in data:
                invoice_data = data["complete_invoice_data"]
                if isinstance(invoice_data, str):
                    try:
                        invoice_data = json.loads(invoice_data)
                    except:
                        invoice_data = {}
                if "poNumber" in invoice_data:
                    data["PO"] = invoice_data["poNumber"]
                    print(f"Debug - Found poNumber in complete_invoice_data: {invoice_data['poNumber']}")
            
            print(f"Debug - Final PO value: {data.get('PO', 'Not set')}")

        # For client 8974121 (Computer Gold), set the delivery challan number
        # Make sure the CNIC field is properly set regardless of how it came in
        if username == "8974121":
            # If the form was submitted (check if CNIC is in the data)
            if "CNIC" in data and data["CNIC"]:
                # It's already set correctly, nothing to do
                pass
            # Check if it's nested in invoiceData
            elif "invoiceData" in data and "CNIC" in data["invoiceData"]:
                data["CNIC"] = data["invoiceData"]["CNIC"]
            # Check if it's in sellerData (as it is in the form)
            elif "sellerData" in data and "CNIC" in data["sellerData"]:
                data["CNIC"] = data["sellerData"]["CNIC"]
            # Check if there's a special field for delivery challan in the data
            elif "deliveryChallan" in data:
                data["CNIC"] = data["deliveryChallan"]
        else:
            # For other clients, ensure CNIC is available (even if empty)
            if "CNIC" not in data:
                data["CNIC"] = ""

        # Calculate totals
        items = data.get("items", [])
        total_excl = 0
        total_tax = 0
        total_further_tax = 0

        buyer_reg = (
            str(
                data.get("buyerRegistrationType")
                or (data.get("buyerData") or {}).get("buyerRegistrationType")
                or (data.get("buyerData") or {}).get("registration_type")
                or ""
            )
            .strip()
            .lower()
        )
        apply_further_tax = username == "0946915" and buyer_reg == "unregistered"

        # Add unit rate for each item if not present
        for item in items:
            try:
                excl = float(str(item.get("valueSalesExcludingST", 0)).replace(",", ""))
                tax = float(str(item.get("salesTaxApplicable", 0)).replace(",", ""))
                qty = float(str(item.get("quantity", 1)).replace(",", ""))

                total_excl += excl
                total_tax += tax

                further_tax_amount = 0
                if apply_further_tax:
                    raw_ft_amount = item.get("furtherTaxAmount")
                    raw_ft_pct = item.get("furtherTaxPercent")
                    raw_ft = item.get("furtherTax", 0)

                    if raw_ft_amount is not None:
                        try:
                            further_tax_amount = float(str(raw_ft_amount).replace(",", ""))
                        except Exception:
                            further_tax_amount = 0
                    else:
                        try:
                            pct_source = raw_ft_pct if raw_ft_pct is not None else raw_ft
                            further_pct = float(str(pct_source).replace("%", ""))
                        except Exception:
                            further_pct = 0

                        if further_pct > 0 and excl > 0:
                            further_tax_amount = round((excl * further_pct) / 100, 2)

                    item["furtherTaxAmount"] = further_tax_amount
                    total_further_tax += further_tax_amount
                else:
                    item["furtherTaxAmount"] = 0

                # Calculate unit rate if not present
                if "unitrate" not in item and qty > 0:
                    item["unitrate"] = excl / qty
            except:
                # Handle any conversion errors
                pass

        # Add totals to data
        data["totalExcl"] = round(total_excl, 2)
        if apply_further_tax:
            data["totalFurtherTax"] = round(total_further_tax, 2)
            data["totalTax"] = round(total_tax + total_further_tax, 2)
            data["totalInclusive"] = round(total_excl + total_tax + total_further_tax, 2)
            data["showFurtherTax"] = data["totalFurtherTax"] > 0
        else:
            data["totalFurtherTax"] = 0
            data["totalTax"] = round(total_tax, 2)
            data["totalInclusive"] = round(total_excl + total_tax, 2)
            data["showFurtherTax"] = False

        # Convert to words with PKR style
        from num2words import num2words

        total = round(data["totalInclusive"], 2)
        amount_in_words = num2words(total, to="currency", lang="en", currency="USD")
        amount_in_words = amount_in_words.replace("dollars", "rupees").replace(
            "cents", "paisa"
        )
        amount_in_words += " only"
        data["amountInWords"] = amount_in_words

        # Generate QR Code as base64
        qr_base64 = ""
        fbr_invoice = data.get("fbrInvoiceNumber", "")
        if fbr_invoice:
            try:
                qr = qrcode.make(fbr_invoice)
                with BytesIO() as buffer:
                    qr.save(buffer)
                    buffer.seek(0)
                    qr_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            except Exception as e:
                print(f"Error generating QR code: {str(e)}")

        # Select the appropriate template based on username - expand with all your clients
        print(f"Selecting template for username: {username}")
        if username == "8974121":
            template_name = "invoice_template.html"
            print(f"Selected template: {template_name} for Computer Gold")
        elif username == "7542425":
            template_name = "invoice_template3.html"
            print(f"Selected template: {template_name} for username 7542425")
        elif username in ["3075270", "0946915", "7542425", "2853653"]:
            template_name = "invoice_template3.html"  # Shared template for these users
            print(f"Selected template: {template_name} for username: {username}")
        else:
            template_name = "invoice_template2.html"
            print(
                f"Selected default template: {template_name} for username: {username}"
            )

        # Store the current data in last_json_data with client_id for future reference
        clean_payload = json.loads(json.dumps(data))
        for item in clean_payload.get("items", []):
            item.pop("furtherTaxAmount", None)
            item.pop("furtherTaxPercent", None)
        clean_payload.pop("totalFurtherTax", None)
        clean_payload.pop("showFurtherTax", None)
        last_json_data[env] = clean_payload

        print(
            f"Final template: {template_name}, Client: {client_id}, STRN: {data.get('sellerSTRN', 'Not set')}"
        )

        # Render HTML invoice with the selected template
        rendered_html = render_template(
            template_name,
            data=data,
            qr_base64=qr_base64,
            client_logo_url=client_logo_url,
            fbr_logo_url=fbr_logo_url,
            username=username,
        )

        # Generate PDF directly to a stream
        pdf_stream = BytesIO()
        HTML(string=rendered_html).write_pdf(pdf_stream)
        pdf_binary = pdf_stream.getvalue()
        pdf_stream.seek(0)

        # Store PDF in database if this invoice was successfully submitted
        try:
            # Find the most recent invoice for this client/env to update with PDF
            cur.execute(
                """UPDATE invoices 
                   SET pdf_data = %s 
                   WHERE id = (
                       SELECT id FROM invoices
                       WHERE client_id = %s AND env = %s AND status = 'Success' 
                       AND pdf_data IS NULL
                       ORDER BY created_at DESC 
                       LIMIT 1
                   )""",
                (pdf_binary, client_id, env)
            )
            conn.commit()
        except Exception as update_error:
            print(f"Error updating PDF data: {update_error}")
            import traceback
            traceback.print_exc()
            # Continue even if PDF storage fails

        cur.close()
        conn.close()

        return send_file(
            pdf_stream,
            mimetype="application/pdf",
            as_attachment=True,
            download_name="invoice.pdf",
        )

    except Exception as e:
        print(f"Error generating PDF from form: {str(e)}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": f"Failed to generate PDF: {str(e)}"}), 500


@app.template_filter("datetimeformat")
def datetimeformat(value):
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").strftime("%d %B %Y")
    except:
        return value


@app.template_filter("comma_format")
def comma_format(value):
    try:
        return "{:,.2f}".format(float(value))
    except (ValueError, TypeError):
        return value


def get_client_config(client_id, env):
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        print(f"Getting client config for client_id: {client_id}, env: {env}")

        cur.execute(
            """
            SELECT sandbox_api_url, sandbox_api_token, production_api_url, production_api_token
            FROM clients
            WHERE id = %s
        """,
            (client_id,),
        )
        row = cur.fetchone()

        cur.close()
        conn.close()

        if not row:
            print(f"No client configuration found for client_id: {client_id}")
            raise Exception("Client configuration not found")

        (
            sandbox_api_url,
            sandbox_api_token,
            production_api_url,
            production_api_token,
        ) = row

        print(f"Retrieved client config successfully for env: {env}")

        if env == "sandbox":
            return {"api_url": sandbox_api_url, "api_token": sandbox_api_token}
        else:
            return {"api_url": production_api_url, "api_token": production_api_token}
    except Exception as e:
        print(f"Error in get_client_config: {str(e)}")
        raise


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT"),
    )


def get_env():
    env = request.args.get("env") or request.headers.get("X-ERP-ENV") or "sandbox"
    return env if env in ["sandbox", "production"] else "sandbox"


add_invoice_form_routes(app, get_db_connection, get_env)
add_draft_invoice_routes(app, get_db_connection, get_env)
add_reports_routes(app, get_db_connection, get_env)

# Store last uploaded file and last JSON per environment
last_uploaded_file = {}
last_json_data = {}


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username")
    password = request.form.get("password")
    env = request.form.get("environment")

    print("Trying to log in with:", username, password)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name FROM users WHERE username = %s AND password_hash = %s",
        (username, password),
    )
    user = cur.fetchone()

    if not user:
        print("No user found.")
        cur.close()
        conn.close()
        return render_template("index.html", error="Invalid username or password")

    user_id = user[0]
    name = user[1]
    print("User ID found:", user_id)

    cur.execute("SELECT id FROM clients WHERE user_id = %s", (user_id,))
    client = cur.fetchone()
    cur.close()
    conn.close()

    if not client:
        print("No client linked to this user.")
        return render_template("index.html", error="Client info missing")

    # Make session permanent and set values
    session.permanent = True
    session["user_id"] = user_id
    session["client_id"] = client[0]
    session["env"] = env
    session["name"] = name

    print("Session created with user_id:", session.get("user_id"))
    return redirect(url_for("dashboard_html"))


@app.before_request
def before_request():
    print("Current session:", dict(session))
    print("Current endpoint:", request.endpoint)

    # List of routes that don't require authentication
    public_routes = ["index", "login", "static"]

    # Check if route needs protection
    if request.endpoint and request.endpoint not in public_routes:
        if "user_id" not in session:
            print("No user_id in session, redirecting to login")
            return redirect(url_for("index"))


@app.route("/records", methods=["GET"])
def get_records():
    env = get_env()
    client_id = session.get("client_id")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT invoice_data, fbr_response, status, created_at
        FROM invoices
        WHERE client_id = %s AND env = %s
        ORDER BY created_at DESC
    """,
        (client_id, env),
    )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    records = []
    for idx, row in enumerate(rows, start=1):
        invoice_data_raw, fbr_response_raw, status, created_at = row

        # Ensure parsed JSON objects
        try:
            invoicedata = (
                json.loads(invoice_data_raw)
                if isinstance(invoice_data_raw, str)
                else invoice_data_raw
            )
        except Exception:
            invoicedata = {}

        try:
            fbr_response = (
                json.loads(fbr_response_raw)
                if isinstance(fbr_response_raw, str)
                else fbr_response_raw
            )
        except Exception:
            fbr_response = {}

        try:
            items = invoicedata.get("items", [])

            # Sum across all items
            value_sales_ex_st = sum(
                float(item.get("valueSalesExcludingST", 0) or 0) for item in items
            )
            sales_tax_applicable = sum(
                float(item.get("salesTaxApplicable", 0) or 0) for item in items
            )

            record = {
                "sr": idx,
                "invoiceReference": (
                    invoicedata.get("fbrInvoiceNumber")
                    or fbr_response.get("invoiceNumber")
                    or "N/A"
                ),
                "invoiceRefNo": invoicedata.get("invoiceRefNo", ""),
                "invoiceType": invoicedata.get("invoiceType", ""),
                "invoiceDate": invoicedata.get("invoiceDate", ""),
                "buyerName": invoicedata.get("buyerBusinessName", ""),
                "sellerName": invoicedata.get("sellerBusinessName", ""),
                "totalValue": value_sales_ex_st + sales_tax_applicable,
                "valueSalesExcludingST": value_sales_ex_st,
                "salesTaxApplicable": sales_tax_applicable,
                "status": status,
                "date": created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "items": items,
            }

            records.append(record)
        except Exception as e:
            print(f"Error parsing row #{idx}: {e}")
            continue

    return jsonify(records)


# New endpoint to delete an invoice
@app.route("/delete-invoice", methods=["POST"])
def delete_invoice():
    # Only allow deletions in sandbox environment
    env = get_env()
    if env != "sandbox":
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Deletion only allowed in sandbox environment",
                }
            ),
            403,
        )

    # Get client ID from session
    client_id = session.get("client_id")
    if not client_id:
        return jsonify({"success": False, "error": "No client ID in session"}), 401

    # Get invoice reference from request
    data = request.get_json()
    invoice_ref = data.get("invoiceReference")
    if not invoice_ref:
        return (
            jsonify({"success": False, "error": "No invoice reference provided"}),
            400,
        )

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Find invoices matching the reference, client ID, and environment
        cur.execute(
            """
            SELECT id FROM invoices 
            WHERE client_id = %s AND env = %s AND 
            (
                (invoice_data::jsonb->>'fbrInvoiceNumber' = %s) OR
                (fbr_response::jsonb->>'invoiceNumber' = %s)
            )
        """,
            (client_id, env, invoice_ref, invoice_ref),
        )

        row = cur.fetchone()

        if not row:
            cur.close()
            conn.close()
            return jsonify({"success": False, "error": "Invoice not found"}), 404

        invoice_id = row[0]

        # Delete the invoice
        cur.execute("DELETE FROM invoices WHERE id = %s", (invoice_id,))
        conn.commit()

        cur.close()
        conn.close()

        return jsonify({"success": True, "message": "Invoice deleted successfully"})

    except Exception as e:
        print(f"Error deleting invoice: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


#  Upload Excel File
@app.route("/upload-excel", methods=["POST"])
def upload_excel():
    env = get_env()
    file = request.files.get("file")
    if not file or not file.filename.endswith((".xlsx", ".xls")):
        return jsonify({"error": "Invalid file format"}), 400
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], f"{env}_{file.filename}")
    file.save(filepath)
    last_uploaded_file[env] = filepath
    return jsonify({"message": "File uploaded successfully"})


# Get JSON Data
@app.route("/get-json", methods=["GET"])
def get_json():
    env = get_env()
    if env not in last_uploaded_file or not os.path.exists(last_uploaded_file[env]):
        print("ERROR: File not found or not uploaded. Env =", env)
        print("last_uploaded_file =", last_uploaded_file)
        return jsonify({"error": "No file uploaded"}), 400

    def safe(val, default=""):
        return default if pd.isna(val) or val in [None, ""] else val

    df = pd.read_excel(last_uploaded_file[env], header=None)

    # Parse sectioned key-value pairs
    section_data = {}
    product_start_index = None

    for i, row in df.iterrows():
        key = str(row[0]).strip() if pd.notna(row[0]) else ""
        val = row[1] if len(row) > 1 else None

        if key.lower() == "productdescription":
            product_start_index = i
            break

        if key and not any(key.startswith(s) for s in ["1)", "2)", "3)", "4)"]):
            section_data[key] = safe(val, "")

    if product_start_index is None:
        print("ERROR: No product section found. Section data:", section_data)
        print("File path:", last_uploaded_file[env])
        return jsonify({"error": "No product section found"}), 400

    product_df = pd.read_excel(last_uploaded_file[env], skiprows=product_start_index)

    items = []
    for _, row in product_df.iterrows():
        try:
            rate_raw = safe(row.get("STrate", ""), "")
            rate = (
                str(rate_raw).strip()
                if isinstance(rate_raw, str)
                else f"{int(float(rate_raw) * 100)}%"
            )

            hs_code_raw = safe(row.get("hsCode", ""))
            hs_code = (
                "{:.4f}".format(float(hs_code_raw))
                if isinstance(hs_code_raw, (int, float)) and not pd.isna(hs_code_raw)
                else str(hs_code_raw).strip()
            )

            item = OrderedDict(
                [
                    ("hsCode", hs_code),
                    ("productDescription", safe(row.get("productDescription"))),
                    ("rate", rate),
                    ("uoM", safe(row.get("uoM"))),
                    ("quantity", round(float(safe(row.get("quantity"), 0)), 2)),
                    ("totalValues", round(float(safe(row.get("totalValues"), 0)), 2)),
                    (
                        "valueSalesExcludingST",
                        round(float(safe(row.get("valueSalesExcludingST"), 0)), 2),
                    ),
                    (
                        "fixedNotifiedValueOrRetailPrice",
                        float(safe(row.get("fixedNotifiedValueOrRetailPrice"), 0)),
                    ),
                    (
                        "salesTaxApplicable",
                        round(float(safe(row.get("salesTaxApplicable"), 0)), 2),
                    ),
                    (
                        "salesTaxWithheldAtSource",
                        float(safe(row.get("salesTaxWithheldAtSource"), 0)),
                    ),
                    ("extraTax", str(safe(row.get("extraTax")))),
                    ("furtherTax", float(safe(row.get("furtherTax"), 0))),
                    ("sroScheduleNo", str(safe(row.get("sroScheduleNo")))),
                    ("fedPayable", float(safe(row.get("fedPayable"), 0))),
                    ("discount", float(safe(row.get("discount"), 0))),
                    ("saleType", str(safe(row.get("saleType")))),
                    ("sroItemSerialNo", str(safe(row.get("sroItemSerialNo")))),
                ]
            )
            items.append(item)
        except Exception as e:
            return (
                jsonify({"error": f"Error parsing row: {row.to_dict()} — {str(e)}"}),
                400,
            )

    raw_date = section_data.get("invoiceDate", "")
    if isinstance(raw_date, datetime.datetime):
        invoice_date = raw_date.strftime("%Y-%m-%d")
    else:
        invoice_date = str(raw_date).strip()

    invoice_json = OrderedDict(
        [
            ("invoiceType", safe(section_data.get("invoiceType"))),
            ("invoiceDate", invoice_date),
            ("sellerNTNCNIC", str(safe(section_data.get("sellerNTNCNIC")))),
            ("sellerBusinessName", safe(section_data.get("sellerBusinessName"))),
            ("sellerProvince", safe(section_data.get("sellerProvince"))),
            ("sellerAddress", safe(section_data.get("sellerAddress"))),
            ("buyerNTNCNIC", str(safe(section_data.get("buyerNTNCNIC")))),
            ("buyerBusinessName", safe(section_data.get("buyerBusinessName"))),
            ("buyerProvince", safe(section_data.get("buyerProvince"))),
            ("buyerAddress", safe(section_data.get("buyerAddress"))),
            ("buyerRegistrationType", safe(section_data.get("buyerRegistrationType"))),
            (
                "invoiceRefNo",
                str(safe(section_data.get("invoiceRefNo", ""))),
            ),  # critical fix
            ("scenarioId", safe(section_data.get("scenarioId"))),
        ]
    )

    # Only include scenarioId if sandbox
    if env == "sandbox":
        invoice_json["scenarioId"] = safe(section_data.get("scenarioId"))

    invoice_json["items"] = items

    last_json_data[env] = invoice_json
    return app.response_class(
        response=json.dumps(invoice_json, indent=2, allow_nan=False),
        mimetype="application/json",
    )


@app.route("/submit-fbr", methods=["POST"])
def submit_fbr():
    env = get_env()
    if env not in last_json_data:
        return jsonify({"error": "No JSON to submit"}), 400

    # Use a separate variable and clear global variable to save memory
    json_data = last_json_data[env].copy()

    if "sellerAddress" in json_data:
        json_data["sellerAddress"] = json_data["sellerAddress"].strip().replace("\n", " ")
    if "buyerAddress" in json_data:
        json_data["buyerAddress"] = json_data["buyerAddress"].strip().replace("\n", " ")
        
    try:
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 400

        config = get_client_config(client_id, env)
        api_url = config["api_url"]
        api_token = config["api_token"]

        print(f"API URL: {api_url}")  # Logging API URL (without token for security)

        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        # Log request data (excluding sensitive info)
        print(f"Submitting data to FBR, env: {env}")

        # Send request to FBR with timeout to prevent worker hanging
        response = requests.post(api_url, headers=headers, json=json_data, timeout=180)
        print(f"FBR API Response status: {response.status_code}")

        # Parse response
        try:
            res_json = response.json()
            print(f"FBR API Response: {res_json}")
        except Exception as e:
            print(f"Failed to parse response as JSON: {str(e)}")
            print(f"Response text: {response.text}")
            res_json = {}

        invoice_no = res_json.get("invoiceNumber", "N/A")
        json_data["fbrInvoiceNumber"] = invoice_no
        last_json_data[env]["fbrInvoiceNumber"] = invoice_no
        is_success = bool(invoice_no and invoice_no != "N/A")

        # If failed, return error without inserting into DB
        if not is_success:
            return (
                jsonify(
                    {
                        "status": "Failed",
                        "status_code": response.status_code,
                        "response_text": response.text,
                    }
                ),
                400,
            )

        # Extract minimal necessary data
        status = "Success"
        date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Generate PDF and store it (only for new invoices from now on)
        pdf_binary = None
        try:
            # Generate PDF using same logic as generate-invoice-excel
            data = json_data.copy()
            items = data["items"]

            # Transform form data structure to flat structure for template compatibility
            # If data came from form (has nested sellerData/buyerData), flatten it
            if "sellerData" in data:
                seller_data = data["sellerData"]
                data["sellerBusinessName"] = seller_data.get("sellerBusinessName", "")
                data["sellerAddress"] = seller_data.get("sellerAddress", "")
                data["sellerProvince"] = seller_data.get("sellerProvince", "")
                data["sellerNTNCNIC"] = seller_data.get("sellerNTNCNIC", "")
                data["sellerSTRN"] = seller_data.get("sellerSTRN", "")
            
            if "buyerData" in data:
                buyer_data = data["buyerData"]
                data["buyerBusinessName"] = buyer_data.get("buyerBusinessName", "")
                data["buyerAddress"] = buyer_data.get("buyerAddress", "")
                data["buyerProvince"] = buyer_data.get("buyerProvince", "")
                data["buyerNTNCNIC"] = buyer_data.get("buyerNTNCNIC", "")
                data["buyerSTRN"] = buyer_data.get("buyerSTRN", "")
                data["buyerRegistrationType"] = buyer_data.get("buyerRegistrationType", "")

            # Calculate totals and add unit rate for each item
            total_excl = 0
            total_tax = 0
            for item in items:
                excl = float(item.get("valueSalesExcludingST", 0))
                tax = float(item.get("salesTaxApplicable", 0))
                qty = float(item.get("quantity", 1))
                
                total_excl += excl
                total_tax += tax
                
                # Calculate unit rate if not present
                if "unitrate" not in item:
                    item["unitrate"] = round(excl / qty, 2) if qty > 0 else 0
            
            data["totalExcl"] = round(total_excl, 2)
            data["totalTax"] = round(total_tax, 2)
            data["totalInclusive"] = round(total_excl + total_tax, 2)

            # Convert to words
            from num2words import num2words
            total = round(data["totalInclusive"], 2)
            amount_in_words = num2words(total, to="currency", lang="en", currency="USD")
            amount_in_words = amount_in_words.replace("dollars", "rupees").replace("cents", "paisa") + " only"
            data["amountInWords"] = amount_in_words

            # Generate QR Code
            import qrcode
            import base64
            fbr_invoice = data.get("fbrInvoiceNumber", "")
            qr_base64 = ""
            if fbr_invoice:
                qr = qrcode.QRCode(version=1, box_size=10, border=2)
                qr.add_data(fbr_invoice)
                qr.make(fit=True)
                img = qr.make_image(fill="black", back_color="white")
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                qr_base64 = base64.b64encode(buffer.getvalue()).decode()

            # Get client logo and username for template selection
            conn_temp = get_db_connection()
            cur_temp = conn_temp.cursor()
            cur_temp.execute("SELECT logo_url FROM clients WHERE id = %s", (client_id,))
            client_row = cur_temp.fetchone()
            client_logo_url = client_row[0] if client_row else None
            
            cur_temp.execute("SELECT u.username FROM users u JOIN clients c ON u.id = c.user_id WHERE c.id = %s", (client_id,))
            user_row = cur_temp.fetchone()
            username = str(user_row[0]).strip() if user_row and user_row[0] is not None else None
            
            cur_temp.execute("SELECT fbr_logo FROM fbr LIMIT 1")
            fbr_row = cur_temp.fetchone()
            fbr_logo_url = fbr_row[0] if fbr_row else None
            cur_temp.close()
            conn_temp.close()

            # Select template
            if username == "8974121":
                template_name = "invoice_template.html"
            elif username == "5207949":
                template_name = "invoice_zeeshanst.html"
            elif username == "7542425":
                template_name = "invoice_template3.html"
            elif username in ["3075270", "0946915", "2853653"]:
                template_name = "invoice_template3.html"
            else:
                template_name = "invoice_template2.html"

            # Render and generate PDF
            rendered_html = render_template(
                template_name,
                data=data,
                qr_base64=qr_base64,
                client_logo_url=client_logo_url,
                fbr_logo_url=fbr_logo_url,
                username=username
            )
            pdf_stream = BytesIO()
            HTML(string=rendered_html).write_pdf(pdf_stream)
            pdf_binary = pdf_stream.getvalue()
        except Exception as pdf_error:
            print(f"Error generating PDF for storage: {pdf_error}")
            import traceback
            traceback.print_exc()
            # Continue without PDF if generation fails

        # Insert into invoices table - use try/finally to ensure connection is closed
        conn = None
        cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO invoices (client_id, env, invoice_data, fbr_response, status, created_at, pdf_data)
                VALUES (%s, %s, %s, %s, %s, NOW(), %s)
            """,
                (client_id, env, json.dumps(json_data), json.dumps(res_json), status, pdf_binary),
            )
            conn.commit()
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        # If this submission originated from a saved draft, mark that draft as submitted
        try:
            # Prefer draft_id from the request body, fallback to in-memory cache
            req_body = request.get_json(silent=True) or {}
            draft_id = req_body.get("draft_id")
            if not draft_id and env in last_json_data:
                # last_json_data may contain a draft reference if the frontend set it
                draft_id = last_json_data[env].get("draft_id")

            if draft_id:
                try:
                    conn2 = get_db_connection()
                    cur2 = conn2.cursor()
                    cur2.execute(
                        """
                        UPDATE invoice_drafts
                        SET status = %s,
                            is_submitted = TRUE,
                            updated_at = NOW()
                        WHERE id = %s AND client_id = %s
                        """,
                        ("submitted", draft_id, client_id),
                    )
                    conn2.commit()
                    cur2.close()
                    conn2.close()
                    print(f"Marked draft {draft_id} as submitted for client {client_id} (env={env})")
                except Exception as e:
                    print(f"Warning: Failed to mark draft {draft_id} as submitted: {e}")
        except Exception as e:
            # Non-fatal: do not block a successful submission if marking draft fails
            print(f"Error checking draft_id after submission: {e}")

        # Return response
        return jsonify({"status": status, "invoiceNumber": invoice_no, "date": date})

    except requests.Timeout:
        print("Request to FBR API timed out")
        return jsonify({"error": "Request to FBR API timed out after 15 seconds"}), 504
    except requests.ConnectionError:
        print("Failed to connect to FBR API server")
        return jsonify({"error": "Failed to connect to FBR API server"}), 503
    except Exception as e:
        print(f"Error in submit_fbr: {str(e)}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# Generate Invoice PDF - optimized to use less memory
@app.route("/generate-invoice-excel", methods=["GET"])
def generate_invoice_excel():
    env = get_env()
    if env not in last_json_data:
        return jsonify({"error": "No JSON data to generate invoice"}), 400

    try:
        # Work with a copy of the data to avoid modifying global state
        data = last_json_data[env].copy()
        items = data["items"]

        # Read from Excel file again to get display-only values
        filepath = last_uploaded_file.get(env)
        if filepath and os.path.exists(filepath):
            df = pd.read_excel(filepath, header=None, keep_default_na=False)

            # --- Extract section data (header fields above the product list) ---
            section_data = {}
            product_start_index = None

            for i, row in df.iterrows():
                key = str(row[0]).strip() if pd.notna(row[0]) else ""
                val = row[1] if len(row) > 1 else None

                # Check where product section starts
                if key.lower() == "productdescription":
                    product_start_index = i
                    break

                # Store key-value pairs from the header section
                if key and not any(key.startswith(s) for s in ["1)", "2)", "3)", "4)"]):
                    section_data[key] = val

            # --- Assign extracted fields to `data` dictionary ---
            data["sellerSTRN"] = section_data.get("sellerSTRN", "")
            data["buyerSTRN"] = section_data.get("buyerSTRN", "")
            data["CNIC"] = section_data.get("CNIC", "")
            data["PO"] = section_data.get("PO#", "")

            # --- Read products table from product_start_index ---
            product_df = pd.read_excel(filepath, skiprows=product_start_index)

            # Extract unit rate for each product item
            for i, item in enumerate(items):
                if i < len(product_df):  # Ensure index is in range
                    try:
                        item["unitrate"] = float(product_df.iloc[i].get("rate", 0))
                    except:
                        item["unitrate"] = 0

        # Calculate totals
        total_excl = 0
        total_tax = 0

        for item in items:
            try:
                excl = float(str(item.get("valueSalesExcludingST", 0)).replace(",", ""))
            except:
                excl = 0
            try:
                tax = float(str(item.get("salesTaxApplicable", 0)).replace(",", ""))
            except:
                tax = 0

            total_excl += excl
            total_tax += tax

        # Add totals to data
        data["totalExcl"] = round(total_excl, 2)
        data["totalTax"] = round(total_tax, 2)
        data["totalInclusive"] = round(total_excl + total_tax, 2)

        from num2words import num2words

        # Convert to words with PKR style
        total = round(data["totalInclusive"], 2)
        amount_in_words = num2words(total, to="currency", lang="en", currency="USD")
        amount_in_words = amount_in_words.replace("dollars", "rupees").replace(
            "cents", "paisa"
        )
        amount_in_words += " only"
        data["amountInWords"] = amount_in_words

        # Get FBR invoice number
        fbr_invoice = data.get("fbrInvoiceNumber", "")

        # --- Generate QR Code as base64 ---
        qr_base64 = ""
        if fbr_invoice:
            try:
                qr = qrcode.make(fbr_invoice)
                with BytesIO() as buffer:
                    qr.save(buffer)
                    buffer.seek(0)
                    qr_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            except Exception as e:
                print(f"Error generating QR code: {str(e)}")
                # Continue without QR code if there's an error

        # Fetch client logo from database
        client_id = session.get("client_id")
        user_id = session.get("user_id")
        client_logo_url = None

        # Fetch required data from DB in a single connection
        conn = None
        cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            # Get username in one query
            cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
            user_row = cur.fetchone()
            username = str(user_row[0]).strip() if user_row and user_row[0] is not None else None

            # Get client logo in another query - removed strn column from the query
            if client_id:
                cur.execute("SELECT logo_url FROM clients WHERE id = %s", (client_id,))
                logo_row = cur.fetchone()
                client_logo_url = logo_row[0] if logo_row else None

            # Fetch FBR logo URL in a third query
            cur.execute("SELECT fbr_logo FROM fbr LIMIT 1;")
            fbr_row = cur.fetchone()
            fbr_logo_url = fbr_row[0] if fbr_row else None
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        # Select the appropriate template based on username
        if username == "8974121":
            template_name = "invoice_template.html"
        elif username == "5207949":
            template_name = "invoice_zeeshanst.html"
        elif username == "7542425":
            template_name = "invoice_template3.html"
        elif username == "3075270":
            template_name = "invoice_template3.html"  # Use appropriate template for Care Pharmaceuticals
        else:
            template_name = "invoice_template3.html"

        # --- Render HTML invoice with the selected template ---
        rendered_html = render_template(
            template_name,
            data=data,
            qr_base64=qr_base64,
            client_logo_url=client_logo_url,
            fbr_logo_url=fbr_logo_url,
        )

        # --- Generate PDF directly to a stream ---
        pdf_stream = BytesIO()
        HTML(string=rendered_html).write_pdf(pdf_stream)
        pdf_stream.seek(0)

        return send_file(
            pdf_stream,
            mimetype="application/pdf",
            as_attachment=True,
            download_name="invoice.pdf",
        )

    except Exception as e:
        print(f"Error generating PDF: {str(e)}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": f"Failed to generate PDF: {str(e)}"}), 500


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard.html")
def dashboard_html():
    print("Dashboard access attempt")
    print("Full session data:", dict(session))
    print("User ID in session:", session.get("user_id"))
    print("Request cookies:", dict(request.cookies))

    if "user_id" not in session:
        print("No user_id in session, redirecting to login")
        return redirect(url_for("index"))

    print("Access granted to dashboard")
    return render_template("dashboard.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
