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
load_dotenv()


app = Flask(__name__)
CORS(app, supports_credentials=True)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Update session configuration
app.secret_key = os.getenv("SECRET_KEY", "myfallbacksecret")
print(app.secret_key)  # Debugging line to check secret key

app.config.update(
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=1),
    SESSION_COOKIE_NAME='erp_session',
    SESSION_COOKIE_PATH='/'
)


def get_client_config(client_id, env):
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT sandbox_api_url, sandbox_api_token, production_api_url, production_api_token
        FROM clients
        WHERE id = %s
    """, (client_id,))
    row = cur.fetchone()
    
    cur.close()
    conn.close()

    if not row:
        raise Exception("Client configuration not found")

    sandbox_api_url, sandbox_api_token, production_api_url, production_api_token = row

    if env == "sandbox":
        return {
            "api_url": sandbox_api_url,
            "api_token": sandbox_api_token
        }
    else:
        return {
            "api_url": production_api_url,
            "api_token": production_api_token
        }




def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT"),
    )

def get_env():
    env = request.args.get('env') or request.headers.get('X-ERP-ENV') or 'sandbox'
    return env if env in ['sandbox', 'production'] else 'sandbox'


# Store last uploaded file and last JSON per environment
last_uploaded_file = {}
last_json_data = {}



@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    env = request.form.get('environment')

    print("Trying to log in with:", username, password)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM users WHERE username = %s AND password_hash = %s", (username, password))
    user = cur.fetchone()

    if not user:
        print("No user found.")
        cur.close()
        conn.close()
        return render_template('index.html', error="Invalid username or password")

    user_id = user[0]
    name = user[1]
    print("User ID found:", user_id)

    cur.execute("SELECT id FROM clients WHERE user_id = %s", (user_id,))
    client = cur.fetchone()
    cur.close()
    conn.close()

    if not client:
        print("No client linked to this user.")
        return render_template('index.html', error="Client info missing")

    # Make session permanent and set values
    session.permanent = True
    session['user_id'] = user_id
    session['client_id'] = client[0]
    session['env'] = env
    session['name'] = name
    
    print("Session created with user_id:", session.get('user_id'))
    return redirect(url_for('dashboard_html'))



@app.before_request
def before_request():
    print("Current session:", dict(session))
    print("Current endpoint:", request.endpoint)
    
    # List of routes that don't require authentication
    public_routes = ['index', 'login', 'static']
    
    # Check if route needs protection
    if request.endpoint and request.endpoint not in public_routes:
        if 'user_id' not in session:
            print("No user_id in session, redirecting to login")
            return redirect(url_for('index'))


import json

@app.route('/records', methods=['GET'])
def get_records():
    env = get_env()
    client_id = session.get('client_id')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT invoice_data, fbr_response, status, created_at
        FROM invoices
        WHERE client_id = %s AND env = %s
        ORDER BY created_at DESC
    """, (client_id, env))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    records = []
    for idx, row in enumerate(rows, start=1):
        invoice_data_raw, fbr_response_raw, status, created_at = row

        # Ensure parsed JSON objects
        try:
            invoicedata = json.loads(invoice_data_raw) if isinstance(invoice_data_raw, str) else invoice_data_raw
        except Exception:
            invoicedata = {}

        try:
            fbr_response = json.loads(fbr_response_raw) if isinstance(fbr_response_raw, str) else fbr_response_raw
        except Exception:
            fbr_response = {}

        try:
            items = invoicedata.get("items", [])

            # Sum across all items
            value_sales_ex_st = sum(float(item.get("valueSalesExcludingST", 0) or 0) for item in items)
            sales_tax_applicable = sum(float(item.get("salesTaxApplicable", 0) or 0) for item in items)

            record = {
                "sr": idx,
                "invoiceReference": (
                    invoicedata.get("fbrInvoiceNumber")
                    or fbr_response.get("invoiceNumber")
                    or "N/A"
                ),
                "invoiceType": invoicedata.get("invoiceType", ""),
                "invoiceDate": invoicedata.get("invoiceDate", ""),
                "buyerName": invoicedata.get("buyerBusinessName", ""),
                "sellerName": invoicedata.get("sellerBusinessName", ""),
                "totalValue": value_sales_ex_st + sales_tax_applicable,
                "valueSalesExcludingST": value_sales_ex_st,
                "salesTaxApplicable": sales_tax_applicable,
                "status": status,
                "date": created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "items": items
            }

            records.append(record)
        except Exception as e:
            print(f"Error parsing row #{idx}: {e}")
            continue

    return jsonify(records)






#  Upload Excel File
@app.route('/upload-excel', methods=['POST'])
def upload_excel():
    env = get_env()
    file = request.files.get('file')
    if not file or not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'Invalid file format'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{env}_{file.filename}")
    file.save(filepath)
    last_uploaded_file[env] = filepath
    return jsonify({'message': 'File uploaded successfully'})



# Get JSON Data
@app.route('/get-json', methods=['GET'])
def get_json():
    env = get_env()
    if env not in last_uploaded_file or not os.path.exists(last_uploaded_file[env]):
        print("ERROR: File not found or not uploaded. Env =", env)
        print("last_uploaded_file =", last_uploaded_file)
        return jsonify({'error': 'No file uploaded'}), 400

    def safe(val, default=""):
        return default if pd.isna(val) or val in [None, ""] else val

    df = pd.read_excel(last_uploaded_file[env], header=None)

    # Parse sectioned key-value pairs
    section_data = {}
    product_start_index = None

    for i, row in df.iterrows():
        key = str(row[0]).strip() if pd.notna(row[0]) else ''
        val = row[1] if len(row) > 1 else None

        if key.lower() == "hscode":
            product_start_index = i
            break

        if key and not any(key.startswith(s) for s in ["1)", "2)", "3)", "4)"]):
            section_data[key] = safe(val, "")

    if product_start_index is None:
        print("ERROR: No product section found. Section data:", section_data)
        print("File path:", last_uploaded_file[env])
        return jsonify({'error': 'No product section found'}), 400

    product_df = pd.read_excel(last_uploaded_file[env], skiprows=product_start_index)

    items = []
    for _, row in product_df.iterrows():
        try:
            rate_raw = safe(row.get("STrate", ""), "")
            rate = str(rate_raw).strip() if isinstance(rate_raw, str) else f"{int(float(rate_raw) * 100)}%"

            hs_code_raw = safe(row.get("hsCode", ""))
            hs_code = (
                "{:.4f}".format(float(hs_code_raw))
                if isinstance(hs_code_raw, (int, float)) and not pd.isna(hs_code_raw)
                else str(hs_code_raw).strip()
            )

            item = OrderedDict([
                ("hsCode", hs_code),
                ("productDescription", safe(row.get("productDescription"))),
                ("rate", rate),
                ("uoM", safe(row.get("uoM"))),
                ("quantity", float(safe(row.get("quantity"), 0))),
                ("totalValues", float(safe(row.get("totalValues"), 0))),
                ("valueSalesExcludingST", float(safe(row.get("valueSalesExcludingST"), 0))),
                ("fixedNotifiedValueOrRetailPrice", float(safe(row.get("fixedNotifiedValueOrRetailPrice"), 0))),
                ("salesTaxApplicable", float(safe(row.get("salesTaxApplicable"), 0))),
                ("salesTaxWithheldAtSource", float(safe(row.get("salesTaxWithheldAtSource"), 0))),
                ("extraTax", str(safe(row.get("extraTax")))),
                ("furtherTax", float(safe(row.get("furtherTax"), 0))),
                ("sroScheduleNo", str(safe(row.get("sroScheduleNo")))),
                ("fedPayable", float(safe(row.get("fedPayable"), 0))),
                ("discount", float(safe(row.get("discount"), 0))),
                ("saleType", str(safe(row.get("saleType")))),
                ("sroItemSerialNo", str(safe(row.get("sroItemSerialNo"))))
            ])
            items.append(item)
        except Exception as e:
            return jsonify({"error": f"Error parsing row: {row.to_dict()} — {str(e)}"}), 400

    raw_date = section_data.get("invoiceDate", "")
    if isinstance(raw_date, datetime.datetime):
        invoice_date = raw_date.strftime("%Y-%m-%d")
    else:
        invoice_date = str(raw_date).strip()

    invoice_json = OrderedDict([
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
        ("invoiceRefNo", str(safe(section_data.get("invoiceRefNo", "")))),  # critical fix
        ("scenarioId", safe(section_data.get("scenarioId"))),
    ])
    
    # Only include scenarioId if sandbox
    if env == 'sandbox':
        invoice_json["scenarioId"] = safe(section_data.get("scenarioId"))

    invoice_json["items"] = items

    last_json_data[env] = invoice_json
    return app.response_class(
        response=json.dumps(invoice_json, indent=2, allow_nan=False),
        mimetype='application/json'
    )



@app.route('/submit-fbr', methods=['POST'])
def submit_fbr():
    env = get_env()
    if env not in last_json_data:
        return jsonify({'error': 'No JSON to submit'}), 400

    client_id = session.get("client_id")
    config = get_client_config(client_id, env)
    api_url = config["api_url"]
    api_token = config["api_token"]

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }

    try:
        # Send request to FBR
        response = requests.post(api_url, headers=headers, json=last_json_data[env])
        
        # Parse response
        try:
            res_json = response.json()
        except Exception:
            res_json = {}

        invoice_no = res_json.get("invoiceNumber", "N/A")
        last_json_data[env]["fbrInvoiceNumber"] = invoice_no
        is_success = bool(invoice_no and invoice_no != "N/A")

        # If failed, return error without inserting into DB
        if not is_success:
            return jsonify({
                "status": "Failed",
                "status_code": response.status_code,
                "response_text": response.text
            }), 400

        # Extract values
        status = "Success"
        date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        invoice_date = last_json_data[env].get("invoiceDate", "")
        items = last_json_data[env]["items"]
        value_sales_ex_st = sum(float(item.get("valueSalesExcludingST", 0) or 0) for item in items)
        sales_tax_applicable = sum(float(item.get("salesTaxApplicable", 0) or 0) for item in items)
        total_value = value_sales_ex_st + sales_tax_applicable

        # Insert into invoices table in Supabase
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO invoices (client_id, env, invoice_data, fbr_response, status, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (
            client_id,
            env,
            json.dumps(last_json_data[env]),
            json.dumps(res_json),
            status
        ))
        conn.commit()
        cur.close()
        conn.close()

        # Return response
        return jsonify({
            "status": status,
            "invoiceNumber": invoice_no,
            "date": date
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500



# Generate Invoice PDF
@app.route('/generate-invoice-excel', methods=['GET'])
def generate_invoice_excel():
    env = get_env()
    if env not in last_json_data:
        return jsonify({'error': 'No JSON data to generate invoice'}), 400

    data = last_json_data[env]
    items = data['items']
    
    
    
    
    
    # Read from Excel file again to get display-only values
    filepath = last_uploaded_file.get(env)
    if filepath and os.path.exists(filepath):
        df = pd.read_excel(filepath, header=None)

        # --- Extract section data like sellerSTRN and buyerSTRN ---
        section_data = {}
        product_start_index = None

        for i, row in df.iterrows():
            key = str(row[0]).strip() if pd.notna(row[0]) else ''
            val = row[1] if len(row) > 1 else None

            if key.lower() == "hscode":
                product_start_index = i
                break

            if key and not any(key.startswith(s) for s in ["1)", "2)", "3)", "4)"]):
                section_data[key] = val

        # Add STRNs to `data` for invoice rendering
        data["sellerSTRN"] = section_data.get("sellerSTRN", "")
        data["buyerSTRN"] = section_data.get("buyerSTRN", "")

        # --- Extract simple rate per item for HTML invoice ---
        product_df = pd.read_excel(filepath, skiprows=product_start_index)
        for i, item in enumerate(items):
            try:
                item["unitrate"] = float(product_df.iloc[i].get("rate", 0))  # simple unit rate
            except:
                item["unitrate"] = 0






    # Calculate totals (in case not done earlier)
    total_excl = 0
    total_tax = 0

    for item in items:
        try:
            excl = float(str(item.get('valueSalesExcludingST', 0)).replace(",", ""))
        except:
            excl = 0
        try:
            tax = float(str(item.get('salesTaxApplicable', 0)).replace(",", ""))
        except:
            tax = 0

        total_excl += excl
        total_tax += tax

    # Add totals to data so template can use them
    data["totalExcl"] = round(total_excl, 2)
    data["totalTax"] = round(total_tax, 2)
    data["totalInclusive"] = round(total_excl + total_tax, 2)

    from num2words import num2words

    # Round to 2 decimals if needed
    total = round(data['totalInclusive'], 2)

    # Convert to words with PKR style
    amount_in_words = num2words(total, to='currency', lang='en', currency='USD')
    amount_in_words = amount_in_words.replace("dollars", "rupees").replace("cents", "paisa")

    # Optional: Add "only" at the end
    amount_in_words += " only"

    # Add to template context
    data['amountInWords'] = amount_in_words


    
    # Get FBR invoice number
    fbr_invoice = data.get("fbrInvoiceNumber", "")

    # --- Generate QR Code as base64 ---
    qr_base64 = ""
    if fbr_invoice:
        qr = qrcode.make(fbr_invoice)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            qr_path = tmp.name
            qr.save(qr_path)

        with open(qr_path, "rb") as qr_file:
            qr_base64 = base64.b64encode(qr_file.read()).decode("utf-8")

        os.remove(qr_path)
         
 
    # Fetch client logo from Supabase
    client_id = session.get('client_id')
    client_logo_url = None
    if client_id:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT logo_url FROM clients WHERE id = %s", (client_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            client_logo_url = row[0]
            
    
    # Fetch FBR logo URL from DB
    fbr_logo_url = None

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT fbr_logo FROM fbr LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row:
        fbr_logo_url = row[0]

            

    # --- Render HTML invoice ---
    rendered_html = render_template(
    'invoice_template.html',
    data=data,
    qr_base64=qr_base64,
    client_logo_url=client_logo_url,
    fbr_logo_url=fbr_logo_url
)

    # --- Generate PDF ---
    pdf_file_path = 'invoice.pdf'
    HTML(string=rendered_html).write_pdf(pdf_file_path)

    return send_file(pdf_file_path, as_attachment=True)




@app.route('/')
def index():
    return render_template('index.html')

@app.route('/dashboard.html')
def dashboard_html():
    print("Dashboard access attempt")
    print("Full session data:", dict(session))
    print("User ID in session:", session.get('user_id'))
    print("Request cookies:", dict(request.cookies))  # Add this line
    
    if 'user_id' not in session:
        print("No user_id in session, redirecting to login")
        return redirect(url_for('index'))
    
    print("Access granted to dashboard")
    return render_template('dashboard.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

    
if __name__ == '__main__':
    app.run(debug=True)
