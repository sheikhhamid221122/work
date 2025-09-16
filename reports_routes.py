"""
Routes for reports and analytics functionality
"""
from flask import request, jsonify, session, render_template, url_for, redirect
import json
from datetime import datetime, timedelta
import calendar
from dateutil.relativedelta import relativedelta
import pandas as pd  # Add this import


def add_reports_routes(app, get_db_connection, get_env):
    def check_url_format():
        if "?" in request.query_string.decode("utf-8"):
            print(
                f"Warning: Found invalid URL parameter format: {request.query_string.decode('utf-8')}"
            )
    
    # Small helper to safely convert values to float (coalesce None/invalid to 0.0)
    def safe_float(v):
        try:
            if v is None:
                return 0.0
            return float(v)
        except Exception:
            return 0.0
    @app.route("/reports.html")
    def reports_html():
        if "user_id" not in session:
            return redirect(url_for("index"))
        return render_template("reports.html")

    @app.route("/api/reports/dashboard", methods=["GET"])
    def get_dashboard_data():
        """Get summary data for the dashboard"""
        # Check URL format
        raw_query = request.query_string.decode("utf-8")
        if "?" in raw_query:
            print(f"Warning: Found invalid URL parameter format: {raw_query}")
            # Fix the query string by replacing ? with &
            fixed_query = raw_query.replace("?", "&")
            print(f"Fixed to: {fixed_query}")
        
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        # Always pull production data for reports, regardless of current environment
        # This ensures users see real business data even when working in sandbox
        env = "production"

        # Get date range parameters
        period = request.args.get("period", "all")
        start_date = None
        end_date = None

        today = datetime.now()

        if period == "today":
            start_date = today.strftime("%Y-%m-%d")
            end_date = start_date
        elif period == "week":
            start_date = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
            end_date = today.strftime("%Y-%m-%d")
        elif period == "month":
            start_date = today.replace(day=1).strftime("%Y-%m-%d")
            end_date = today.strftime("%Y-%m-%d")
        elif period == "year":
            start_date = today.replace(month=1, day=1).strftime("%Y-%m-%d")
            end_date = today.strftime("%Y-%m-%d")
        elif period == "custom":
            start_date = request.args.get("start_date")
            end_date = request.args.get("end_date")

            if not start_date or not end_date:
                return (
                    jsonify(
                        {
                            "error": "Custom date range requires start_date and end_date parameters"
                        }
                    ),
                    400,
                )

        conn = get_db_connection()
        cur = conn.cursor()

        # Common date filter condition
        date_condition = ""
        params = [client_id, env]

        if start_date and end_date:
            date_condition = "AND COALESCE((invoice_data::jsonb->>'invoiceDate')::date, DATE(created_at)) BETWEEN %s AND %s"
            params.extend([start_date, end_date])

        # Initialize defaults so response can be built even if some queries fail
        total_invoices = 0
        total_sales = 0.0
        total_tax = 0.0
        unique_buyers = 0
        unique_products = 0
        time_series_data = []
        top_products = []
        top_buyers = []
        tax_breakdown = []

        try:
            # Get total successful invoices - count invoices
            cur.execute(
                f"""
                SELECT COUNT(*) 
                FROM invoices 
                WHERE client_id = %s 
                AND env = %s 
                AND status = 'Success'
                {date_condition}
                """,
                params,
            )
            total_invoices = cur.fetchone()[0] or 0

            # Get total sales value (sum of all invoice totals)
            cur.execute(
                f"""
                WITH extracted_values AS (
                    SELECT 
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                    CASE 
                                        WHEN invoice_data::jsonb ? 'items' THEN 
                                            jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].totalValues')
                                        ELSE 
                                            '[]'::jsonb 
                                    END
                                ELSE 
                                    '[]'::jsonb 
                            END
                        )::numeric AS val
                    FROM invoices 
                    WHERE client_id = %s 
                    AND env = %s 
                    AND status = 'Success'
                    {date_condition}
                )
                SELECT COALESCE(SUM(val), 0) FROM extracted_values
                """,
                params,
            )
            total_sales = safe_float(cur.fetchone()[0] or 0)

            # Get total tax collected
            cur.execute(
                f"""
                WITH extracted_values AS (
                    SELECT 
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                    CASE 
                                        WHEN invoice_data::jsonb ? 'items' THEN 
                                            jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].salesTaxApplicable')
                                        ELSE 
                                            '[]'::jsonb 
                                    END
                                ELSE 
                                    '[]'::jsonb 
                            END
                        )::numeric AS val
                    FROM invoices 
                    WHERE client_id = %s 
                    AND env = %s 
                    AND status = 'Success'
                    {date_condition}
                )
                SELECT COALESCE(SUM(val), 0) FROM extracted_values
                """,
                params,
            )
            total_tax = safe_float(cur.fetchone()[0] or 0)

            # Get unique buyers count
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT 
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                            CASE 
                                WHEN invoice_data::jsonb ? 'buyerBusinessName' THEN 
                                    invoice_data::jsonb->>'buyerBusinessName'
                                WHEN invoice_data::jsonb ? 'buyerData' AND 
                                    jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object' AND
                                    invoice_data::jsonb->'buyerData' ? 'buyerBusinessName' THEN
                                    invoice_data::jsonb->'buyerData'->>'buyerBusinessName'
                                ELSE 
                                    NULL
                            END
                        ELSE 
                            NULL
                    END)
                FROM invoices 
                WHERE client_id = %s 
                AND env = %s 
                AND status = 'Success'
                {date_condition}
                """,
                params,
            )
            unique_buyers = cur.fetchone()[0] or 0

            # Get unique products count
            cur.execute(
                f"""
                WITH product_descriptions AS (
                        SELECT DISTINCT COALESCE(elem->>'productDescription', elem->>'description', elem->>'productName', elem->>'name') as product_name
                        FROM invoices,
                        LATERAL jsonb_array_elements(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                                    invoice_data::jsonb ? 'items' AND
                                    jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                                THEN invoice_data::jsonb->'items'
                                ELSE '[]'::jsonb
                            END
                        ) as elem
                        WHERE client_id = %s 
                        AND env = %s 
                        AND status = 'Success'
                        {date_condition}
                    )
                    SELECT COUNT(*) FROM product_descriptions WHERE product_name IS NOT NULL
                """,
                params,
            )
            unique_products = cur.fetchone()[0] or 0

            # Get time series data for sales over time
            time_series_data = get_time_series_data(
                cur, client_id, env, period, start_date, end_date
            )

            # Get top 5 products by sales
            top_products = get_top_products(cur, client_id, env, start_date, end_date)

            # Get top 5 buyers by sales
            top_buyers = get_top_buyers(cur, client_id, env, start_date, end_date)

            # Get tax breakdown
            tax_breakdown = get_tax_breakdown(cur, client_id, env, start_date, end_date)

        except Exception as e:
            print(f"Error in dashboard data: {str(e)}")
            # Keep defaults and return partial data instead of crashing

        cur.close()
        conn.close()

        return jsonify(
            {
                "summary": {
                    "total_invoices": total_invoices,
                    "total_sales": round(total_sales, 2),
                    "total_tax": round(total_tax, 2),
                    "unique_buyers": unique_buyers,
                    "unique_products": unique_products,
                    "revenue_excluding_tax": round(total_sales - total_tax, 2),
                    "average_invoice_value": round(
                        total_sales / total_invoices if total_invoices > 0 else 0, 2
                    ),
                },
                "time_series": time_series_data,
                "top_products": top_products,
                "top_buyers": top_buyers,
                "tax_breakdown": tax_breakdown,
            }
        )

    def get_time_series_data(cur, client_id, env, period, start_date, end_date):
        """Get time series data for the dashboard charts"""
        format_string = "%Y-%m-%d"
        # Prefer invoice date stored inside invoice_data JSON when present,
        # otherwise fall back to created_at. We'll use DATE_TRUNC on the
        # COALESCE of invoiceDate (casted to timestamp) and created_at.
        date_trunc = "day"

        if period == "year":
            format_string = "%Y-%m"
            date_trunc = "month"
        elif period == "all":
            format_string = "%Y-%m"
            date_trunc = "month"

        # Common date filter condition
        date_condition = ""
        params = [client_id, env]

        if start_date and end_date:
            # Filter using invoice date when available, otherwise use created_at
            date_condition = "AND COALESCE((invoice_data::jsonb->>'invoiceDate')::date, DATE(created_at)) BETWEEN %s AND %s"
            params.extend([start_date, end_date])

        # Get daily/monthly sales - FIXED
        cur.execute(
            f"""
            WITH time_periods AS (
                SELECT 
                    DATE_TRUNC('{date_trunc}', COALESCE((invoice_data::jsonb->>'invoiceDate')::timestamp, created_at)) as period,
                    invoice_data,
                    id
                FROM invoices 
                WHERE client_id = %s 
                AND env = %s 
                AND status = 'Success'
                {date_condition}
            ),
            invoice_counts AS (
                SELECT period, COUNT(*) as invoice_count
                FROM time_periods
                GROUP BY period
            ),
            sales_data AS (
                SELECT 
                    period,
                    SUM(val) as total_sales
                FROM (
                    SELECT 
                        period,
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                    CASE 
                                        WHEN invoice_data::jsonb ? 'items' THEN 
                                            jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].totalValues')
                                        ELSE 
                                            '[]'::jsonb 
                                    END
                                ELSE 
                                    '[]'::jsonb 
                            END
                        )::numeric AS val
                    FROM time_periods
                ) as extracted_values
                GROUP BY period
            ),
            tax_data AS (
                SELECT 
                    period,
                    SUM(val) as total_tax
                FROM (
                    SELECT 
                        period,
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                    CASE 
                                        WHEN invoice_data::jsonb ? 'items' THEN 
                                            jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].salesTaxApplicable')
                                        ELSE 
                                            '[]'::jsonb 
                                    END
                                ELSE 
                                    '[]'::jsonb 
                            END
                        )::numeric AS val
                    FROM time_periods
                ) as extracted_values
                GROUP BY period
            )
            SELECT 
                ic.period,
                ic.invoice_count,
                COALESCE(sd.total_sales, 0) as total_sales,
                COALESCE(td.total_tax, 0) as total_tax
            FROM invoice_counts ic
            LEFT JOIN sales_data sd ON ic.period = sd.period
            LEFT JOIN tax_data td ON ic.period = td.period
            ORDER BY ic.period
            """,
            params,
        )

        time_series = []
        for row in cur.fetchall():
            period_date, invoice_count, total_sales, total_tax = row
            ts_total_sales = safe_float(total_sales)
            ts_total_tax = safe_float(total_tax)
            time_series.append(
                {
                    "period": period_date.strftime(format_string),
                    "invoice_count": invoice_count or 0,
                    "total_sales": ts_total_sales,
                    "total_tax": ts_total_tax,
                    "sales_excluding_tax": ts_total_sales - ts_total_tax,
                }
            )

        return time_series

    def get_top_products(cur, client_id, env, start_date, end_date):
        """Get top 5 products by sales volume"""
        # Common date filter condition
        date_condition = ""
        params = [client_id, env]

        if start_date and end_date:
            date_condition = "AND COALESCE((invoice_data::jsonb->>'invoiceDate')::date, DATE(created_at)) BETWEEN %s AND %s"
            params.extend([start_date, end_date])

        cur.execute(
            f"""
            WITH product_items AS (
                SELECT 
                    COALESCE(i.productDescription, i.description, i.productName, i.name) as productDescription,
                    i.quantity::numeric,
                    i.totalValues::numeric,
                    i.salesTaxApplicable::numeric
                FROM invoices, 
                jsonb_to_recordset(
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                             invoice_data::jsonb ? 'items' AND
                             jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                        THEN invoice_data::jsonb->'items'
                        ELSE '[]'::jsonb
                    END
                ) AS i(productDescription text, description text, productName text, name text, quantity text, totalValues text, salesTaxApplicable text)
                WHERE client_id = %s 
                AND env = %s 
                AND status = 'Success'
                {date_condition}
                AND COALESCE(i.productDescription, i.description, i.productName, i.name) IS NOT NULL
            )
            SELECT 
                productDescription,
                SUM(quantity) as total_quantity,
                SUM(totalValues) as total_sales,
                SUM(salesTaxApplicable) as total_tax
            FROM product_items
            GROUP BY productDescription
            ORDER BY total_sales DESC
            LIMIT 5
            """,
            params,
        )

        top_products = []
        for row in cur.fetchall():
            product_name, quantity, total_sales, total_tax = row
            q = safe_float(quantity)
            ts = safe_float(total_sales)
            tt = safe_float(total_tax)
            top_products.append(
                {
                    "product_name": product_name or "",
                    "quantity": q,
                    "total_sales": ts,
                    "total_tax": tt,
                    "sales_excluding_tax": ts - tt,
                }
            )

        return top_products

    def get_top_buyers(cur, client_id, env, start_date, end_date):
        """Get top 5 buyers by purchase amount"""
        # Common date filter condition
        date_condition = ""
        params = [client_id, env]

        if start_date and end_date:
            date_condition = "AND COALESCE((invoice_data::jsonb->>'invoiceDate')::date, DATE(created_at)) BETWEEN %s AND %s"
            params.extend([start_date, end_date])

        # FIXED buyer query
        cur.execute(
            f"""
            WITH buyers AS (
                SELECT
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                            CASE 
                                WHEN invoice_data::jsonb ? 'buyerBusinessName' THEN 
                                    invoice_data::jsonb->>'buyerBusinessName'
                                WHEN invoice_data::jsonb ? 'buyerData' AND 
                                     jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object' AND
                                     invoice_data::jsonb->'buyerData' ? 'buyerBusinessName' THEN
                                    invoice_data::jsonb->'buyerData'->>'buyerBusinessName'
                                ELSE 'Unknown Buyer'
                            END
                        ELSE 'Unknown Buyer'
                    END as buyer_name,
                    invoice_data,
                    id
                FROM invoices 
                WHERE client_id = %s 
                AND env = %s 
                AND status = 'Success'
                {date_condition}
            ),
            buyer_totals AS (
                SELECT
                    buyer_name,
                    COUNT(*) as invoice_count,
                    SUM(val) as total_purchase
                FROM (
                    SELECT 
                        buyer_name,
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                    CASE 
                                        WHEN invoice_data::jsonb ? 'items' THEN 
                                            jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].totalValues')
                                        ELSE 
                                            '[]'::jsonb 
                                    END
                                ELSE 
                                    '[]'::jsonb 
                            END
                        )::numeric AS val
                    FROM buyers
                ) as extracted_values
                GROUP BY buyer_name
            )
            SELECT 
                buyer_name,
                total_purchase,
                invoice_count
            FROM buyer_totals
            WHERE buyer_name != 'Unknown Buyer'
            ORDER BY total_purchase DESC
            LIMIT 5
            """,
            params,
        )

        top_buyers = []
        for row in cur.fetchall():
            buyer_name, total_purchase, invoice_count = row
            tp = safe_float(total_purchase)
            ic = invoice_count or 0
            top_buyers.append(
                {
                    "buyer_name": buyer_name,
                    "total_purchase": tp,
                    "invoice_count": ic,
                    "average_purchase": round(tp / ic, 2) if ic > 0 else 0,
                }
            )

        return top_buyers

    def get_tax_breakdown(cur, client_id, env, start_date, end_date):
        """Get tax breakdown by tax rate"""
        # Common date filter condition
        date_condition = ""
        params = [client_id, env]

        if start_date and end_date:
            date_condition = "AND DATE(created_at) BETWEEN %s AND %s"
            params.extend([start_date, end_date])

        cur.execute(
            f"""
            WITH tax_items AS (
                SELECT 
                    COALESCE(i.rate, '0%%') as tax_rate,
                    i.salesTaxApplicable::numeric as tax_amount
                FROM invoices, 
                jsonb_to_recordset(
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                             invoice_data::jsonb ? 'items' AND
                             jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                        THEN invoice_data::jsonb->'items'
                        ELSE '[]'::jsonb
                    END
                ) AS i(rate text, salesTaxApplicable text)
                WHERE client_id = %s 
                AND env = %s 
                AND status = 'Success'
                {date_condition}
            )
            SELECT 
                tax_rate,
                SUM(tax_amount) as total_tax
            FROM tax_items
            GROUP BY tax_rate
            ORDER BY total_tax DESC
            """,
            params,
        )

        print(f"Tax breakdown params: {params}")

        tax_breakdown = []
        for row in cur.fetchall():
            tax_rate, total_tax = row
            tax_breakdown.append({"tax_rate": tax_rate, "total_tax": safe_float(total_tax)})

        return tax_breakdown

    @app.route("/api/reports/invoices", methods=["GET"])
    def get_invoice_list():
        """Get list of invoices with filtering options"""
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        # Always pull production data for reports, regardless of current environment
        env = "production"

        # Pagination parameters
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
        offset = (page - 1) * per_page

        # Filtering parameters
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        buyer_name = request.args.get("buyer_name", "").strip()
        invoice_ref = request.args.get("invoice_ref", "").strip()
        product_name = request.args.get("product_name", "").strip()

        # Sorting parameters
        sort_field = request.args.get("sort_field", "created_at")
        sort_order = request.args.get("sort_order", "desc").upper()

        # Validate sort parameters
        valid_sort_fields = ["created_at", "invoice_ref", "buyer_name", "total_amount"]
        if sort_field not in valid_sort_fields:
            sort_field = "created_at"

        if sort_order not in ["ASC", "DESC"]:
            sort_order = "DESC"

        conn = get_db_connection()
        cur = conn.cursor()

        # Build WHERE clause and parameters
        where_conditions = ["client_id = %s", "env = %s", "status = 'Success'"]
        params = [client_id, env]

        if start_date and end_date:
            where_conditions.append("COALESCE((invoice_data::jsonb->>'invoiceDate')::date, DATE(created_at)) BETWEEN %s AND %s")
            params.extend([start_date, end_date])

        # Add buyer name filter if provided
        if buyer_name:
            where_conditions.append(
                """
                (
                    (jsonb_typeof(invoice_data::jsonb) = 'object' AND
                     invoice_data::jsonb ? 'buyerBusinessName' AND
                     LOWER(invoice_data::jsonb->>'buyerBusinessName') LIKE LOWER(%s))
                    OR
                    (jsonb_typeof(invoice_data::jsonb) = 'object' AND
                     invoice_data::jsonb ? 'buyerData' AND
                     jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object' AND
                     invoice_data::jsonb->'buyerData' ? 'buyerBusinessName' AND
                     LOWER(invoice_data::jsonb->'buyerData'->>'buyerBusinessName') LIKE LOWER(%s))
                )
            """
            )
            search_term = f"%{buyer_name}%"
            params.extend([search_term, search_term])

        # Add invoice reference filter if provided - prefer invoiceRefNo, fall back to fbr_response.invoiceNumber
        if invoice_ref:
            where_conditions.append(
                """
                (
                    (jsonb_typeof(invoice_data::jsonb) = 'object' AND
                     invoice_data::jsonb ? 'invoiceRefNo' AND
                     LOWER(invoice_data::jsonb->>'invoiceRefNo') LIKE LOWER(%s))
                    OR
                    (jsonb_typeof(fbr_response::jsonb) = 'object' AND
                     fbr_response::jsonb ? 'invoiceNumber' AND
                     LOWER(fbr_response::jsonb->>'invoiceNumber') LIKE LOWER(%s))
                )
            """
            )
            search_term = f"%{invoice_ref}%"
            params.extend([search_term, search_term])

        # Add product name filter if provided
        if product_name:
            where_conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                        CASE 
                            WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                                 invoice_data::jsonb ? 'items' AND
                                 jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                            THEN invoice_data::jsonb->'items'
                            ELSE '[]'::jsonb
                        END
                    ) as item
                    WHERE LOWER(item->>'productDescription') LIKE LOWER(%s)
                )
            """
            )
            params.append(f"%{product_name}%")

        # Construct the WHERE clause
        where_clause = " AND ".join(where_conditions)

        # Build sorting
        order_by = ""
        if sort_field == "created_at":
            order_by = f"created_at {sort_order}"
        elif sort_field == "invoice_ref":
            order_by = f"""
                COALESCE(
                CASE WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND invoice_data::jsonb ? 'invoiceRefNo'
                    THEN invoice_data::jsonb->>'invoiceRefNo'
                    ELSE NULL
                END,
                    CASE WHEN jsonb_typeof(fbr_response::jsonb) = 'object' AND fbr_response::jsonb ? 'invoiceNumber'
                         THEN fbr_response::jsonb->>'invoiceNumber'
                         ELSE NULL
                    END
                ) {sort_order} NULLS LAST
            """
        elif sort_field == "buyer_name":
            order_by = f"""
                COALESCE(
                    CASE WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND invoice_data::jsonb ? 'buyerBusinessName'
                         THEN invoice_data::jsonb->>'buyerBusinessName'
                         ELSE NULL
                    END,
                    CASE WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND invoice_data::jsonb ? 'buyerData'
                         AND jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object'
                         AND invoice_data::jsonb->'buyerData' ? 'buyerBusinessName'
                         THEN invoice_data::jsonb->'buyerData'->>'buyerBusinessName'
                         ELSE NULL
                    END
                ) {sort_order} NULLS LAST
            """
        elif sort_field == "total_amount":
            # Fixed the total_amount sorting - use more direct approach
            order_by = f"""
                (
                    SELECT COALESCE(SUM(
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                                     invoice_data::jsonb ? 'items' 
                                THEN jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].totalValues')
                                ELSE '[]'::jsonb
                            END
                        )::numeric
                    ), 0)
                ) {sort_order}
            """

        # Count total results for pagination
        count_query = f"""
            SELECT COUNT(*)
            FROM invoices
            WHERE {where_clause}
        """
        cur.execute(count_query, params)
        total_count = cur.fetchone()[0]

        # Main query with pagination
        main_query = f"""
            SELECT 
                id,
                created_at,
                invoice_data,
                fbr_response
            FROM invoices
            WHERE {where_clause}
            ORDER BY {order_by}
            LIMIT %s OFFSET %s
        """

        # Add pagination parameters
        params.extend([per_page, offset])

        cur.execute(main_query, params)

        invoices = []
        for row in cur.fetchall():
            id, created_at, invoice_data_raw, fbr_response_raw = row

            # Parse JSON data
            try:
                invoice_data = (
                    json.loads(invoice_data_raw)
                    if isinstance(invoice_data_raw, str)
                    else invoice_data_raw
                )
            except Exception:
                invoice_data = {}

            try:
                fbr_response = (
                    json.loads(fbr_response_raw)
                    if isinstance(fbr_response_raw, str)
                    else fbr_response_raw
                )
            except Exception:
                fbr_response = {}

            # Extract invoice reference number - prefer invoiceRefNo
            invoice_ref = (
                invoice_data.get("invoiceRefNo")
                or invoice_data.get("fbrInvoiceNumber")
                or fbr_response.get("invoiceNumber")
                or "N/A"
            )

            # Extract buyer name
            buyer_name = "Unknown Buyer"
            if "buyerBusinessName" in invoice_data:
                buyer_name = invoice_data["buyerBusinessName"]
            elif (
                "buyerData" in invoice_data
                and "buyerBusinessName" in invoice_data["buyerData"]
            ):
                buyer_name = invoice_data["buyerData"]["buyerBusinessName"]

            # Extract seller name
            seller_name = "Unknown Seller"
            if "sellerBusinessName" in invoice_data:
                seller_name = invoice_data["sellerBusinessName"]
            elif (
                "sellerData" in invoice_data
                and "sellerBusinessName" in invoice_data["sellerData"]
            ):
                seller_name = invoice_data["sellerData"]["sellerBusinessName"]

            # Extract invoice date
            invoice_date = None
            if "invoiceDate" in invoice_data:
                invoice_date = invoice_data["invoiceDate"]

            # Extract items and calculate totals
            items = invoice_data.get("items", [])
            total_value_excl = 0
            total_tax = 0

            for item in items:
                try:
                    value_excl = safe_float(item.get("valueSalesExcludingST", 0))
                    tax = safe_float(item.get("salesTaxApplicable", 0))
                    total_value_excl += value_excl
                    total_tax += tax
                except (ValueError, TypeError):
                    pass

            total_amount = total_value_excl + total_tax

            # Simplified items list
            simplified_items = []
            for item in items:
                try:
                    quantity = safe_float(item.get("quantity", 0))
                    value_excl = safe_float(item.get("valueSalesExcludingST", 0))
                    tax = safe_float(item.get("salesTaxApplicable", 0))
                    total = safe_float(item.get("totalValues", 0))

                    # Safely calculate rate
                    rate = 0
                    if quantity > 0:
                        rate = value_excl / quantity

                    simplified_items.append(
                        {
                            "description": item.get("productDescription", ""),
                            "quantity": quantity,
                            "unit": item.get("uoM", ""),
                            "rate": rate,
                            "value_excl": value_excl,
                            "tax": tax,
                            "total": total,
                        }
                    )
                except (ValueError, TypeError):
                    # Skip items with conversion errors
                    pass

            invoice_obj = {
                "id": id,
                "invoice_ref": invoice_ref,
                "buyer_name": buyer_name,
                "seller_name": seller_name,
                "invoice_date": invoice_date,
                "created_at": created_at.isoformat(),
                "total_value_excl": round(total_value_excl, 2),
                "total_tax": round(total_tax, 2),
                "total_amount": round(total_amount, 2),
                "items": simplified_items,
            }

            invoices.append(invoice_obj)

        cur.close()
        conn.close()

        # Calculate pagination info
        total_pages = (total_count + per_page - 1) // per_page  # Ceiling division

        return jsonify(
            {
                "invoices": invoices,
                "pagination": {
                    "current_page": page,
                    "per_page": per_page,
                    "total_items": total_count,
                    "total_pages": total_pages,
                },
            }
        )

    @app.route("/api/reports/invoice/<invoice_id>", methods=["GET"])
    def get_invoice_detail(invoice_id):
        """Get detailed information about a specific invoice (accepts UUID/text id).
        Ensures the invoice belongs to the logged-in client's requested env.
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        # Allow client to request specific env via query param (apiUrl adds env)
        env = request.args.get('env') or get_env()

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT 
                id,
                created_at,
                invoice_data,
                fbr_response
            FROM invoices
            WHERE id = %s AND client_id = %s AND env = %s
            """,
            [invoice_id, client_id, env],
        )

        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "Invoice not found or access denied"}), 404

        id, created_at, invoice_data_raw, fbr_response_raw = row

        # Parse JSON data
        try:
            invoice_data = (
                json.loads(invoice_data_raw)
                if isinstance(invoice_data_raw, str)
                else invoice_data_raw
            )
        except Exception:
            invoice_data = {}

        try:
            fbr_response = (
                json.loads(fbr_response_raw)
                if isinstance(fbr_response_raw, str)
                else fbr_response_raw
            )
        except Exception:
            fbr_response = {}

        # Extract all available invoice details - prefer invoiceRefNo, fall back to fbrInvoiceNumber then fbr response
        invoice_ref = (
            invoice_data.get("invoiceRefNo")
            or invoice_data.get("fbrInvoiceNumber")
            or fbr_response.get("invoiceNumber")
            or "N/A"
        )

        # Process seller information
        seller_info = {}
        if "sellerBusinessName" in invoice_data:
            seller_info["name"] = invoice_data["sellerBusinessName"]
        elif (
            "sellerData" in invoice_data
            and "sellerBusinessName" in invoice_data["sellerData"]
        ):
            seller_info["name"] = invoice_data["sellerData"]["sellerBusinessName"]
        else:
            seller_info["name"] = "Unknown Seller"

        # Add more seller details
        seller_fields = [
            ("ntn_cnic", "sellerNTNCNIC", "NTN/CNIC"),
            ("strn", "sellerSTRN", "STRN"),
            ("address", "sellerAddress", "Address"),
            ("province", "sellerProvince", "Province"),
        ]

        for field_key, json_key, display_name in seller_fields:
            if json_key in invoice_data:
                seller_info[field_key] = invoice_data[json_key]
            elif (
                "sellerData" in invoice_data
                and json_key.replace("seller", "seller") in invoice_data["sellerData"]
            ):
                seller_info[field_key] = invoice_data["sellerData"][
                    json_key.replace("seller", "seller")
                ]
            else:
                seller_info[field_key] = ""

        # Process buyer information
        buyer_info = {}
        if "buyerBusinessName" in invoice_data:
            buyer_info["name"] = invoice_data["buyerBusinessName"]
        elif (
            "buyerData" in invoice_data
            and "buyerBusinessName" in invoice_data["buyerData"]
        ):
            buyer_info["name"] = invoice_data["buyerData"]["buyerBusinessName"]
        else:
            buyer_info["name"] = "Unknown Buyer"

        # Add more buyer details
        buyer_fields = [
            ("ntn_cnic", "buyerNTNCNIC", "NTN/CNIC"),
            ("strn", "buyerSTRN", "STRN"),
            ("address", "buyerAddress", "Address"),
            ("province", "buyerProvince", "Province"),
            ("registration_type", "buyerRegistrationType", "Registration Type"),
        ]

        for field_key, json_key, display_name in buyer_fields:
            if json_key in invoice_data:
                buyer_info[field_key] = invoice_data[json_key]
            elif (
                "buyerData" in invoice_data
                and json_key.replace("buyer", "buyer") in invoice_data["buyerData"]
            ):
                buyer_info[field_key] = invoice_data["buyerData"][
                    json_key.replace("buyer", "buyer")
                ]
            else:
                buyer_info[field_key] = ""

        # Process invoice details
        invoice_details = {
            "invoice_ref": invoice_ref,
            "invoice_type": invoice_data.get("invoiceType", ""),
            "invoice_date": invoice_data.get("invoiceDate", ""),
            "created_at": created_at.isoformat(),
            "po_number": invoice_data.get("poNumber", invoice_data.get("PO", "")),
            "delivery_challan": invoice_data.get("CNIC", ""),
            "invoice_ref_no": invoice_data.get("invoiceRefNo", ""),
        }

        # Process items and calculate totals
        items = invoice_data.get("items", [])
        processed_items = []
        total_value_excl = 0
        total_tax = 0

        for item in items:
            try:
                quantity = safe_float(item.get("quantity", 0))
                value_excl = safe_float(item.get("valueSalesExcludingST", 0))
                tax = safe_float(item.get("salesTaxApplicable", 0))
                total = safe_float(item.get("totalValues", 0))

                total_value_excl += value_excl
                total_tax += tax

                processed_items.append(
                    {
                        "description": item.get("productDescription", ""),
                        "hs_code": item.get("hsCode", ""),
                        "quantity": quantity,
                        "unit": item.get("uoM", ""),
                        "rate": value_excl / quantity if quantity > 0 else 0,
                        "value_excl": value_excl,
                        "tax_rate": item.get("rate", "0%"),
                        "tax": tax,
                        "total": total,
                        "sale_type": item.get("saleType", ""),
                        "sro_schedule_no": item.get("sroScheduleNo", ""),
                        "sro_item_serial_no": item.get("sroItemSerialNo", ""),
                    }
                )
            except (ValueError, TypeError):
                # Skip malformed items
                pass

        total_amount = total_value_excl + total_tax

        # Calculate tax breakdown
        tax_breakdown = {}
        for item in processed_items:
            tax_rate = item["tax_rate"]
            if tax_rate not in tax_breakdown:
                tax_breakdown[tax_rate] = 0
            tax_breakdown[tax_rate] += item["tax"]

        tax_summary = []
        for rate, amount in tax_breakdown.items():
            tax_summary.append({"rate": rate, "amount": round(amount, 2)})

        cur.close()
        conn.close()

        return jsonify(
            {
                "invoice_details": invoice_details,
                "seller_info": seller_info,
                "buyer_info": buyer_info,
                "items": processed_items,
                "totals": {
                    "total_value_excl": round(total_value_excl, 2),
                    "total_tax": round(total_tax, 2),
                    "total_amount": round(total_amount, 2),
                },
                "tax_summary": tax_summary,
                "fbr_response": fbr_response,
            }
        )

    @app.route("/api/reports/product-analytics", methods=["GET"])
    def get_product_analytics():
        """Get product-specific analytics"""
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        # Use current session environment (fall back to server environment)
        env = "production"

        # Filtering parameters
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        product_name = request.args.get("product_name", "").strip()

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            # First attempt with current environment
            products = get_products_for_env(cur, client_id, env, start_date, end_date, product_name)
            
            # Debug output
            print(f"Products API: Found {len(products)} products (env={env})")
            
            # If no products found and not in production, try with production environment
            if len(products) == 0 and env != 'production':
                print("Products API: no products found for session env, retrying with env='production'")
                products = get_products_for_env(cur, client_id, 'production', start_date, end_date, product_name)
                print(f"Products API (production retry): Found {len(products)} products (env=production)")
                
                # If we found products in production, use that environment for the rest of the queries
                if len(products) > 0:
                    env = 'production'
            
            # Get top 5 products for trends and distributions
            top_products = [p["product_name"] for p in products[:5]]
            
            # Get monthly trends if we have top products
            monthly_trends = []
            if top_products:
                monthly_trends = get_product_monthly_trends(cur, client_id, env, top_products, start_date, end_date)
                print(f"Products API: Monthly trends has {len(monthly_trends)} entries")
            
            # Get buyer distribution data
            buyer_distribution = {}
            if products:
                buyer_distribution = get_product_buyer_distribution(cur, client_id, env, product_name, start_date, end_date)
                print(f"Products API: Buyer distribution has {len(buyer_distribution)} entries")
            
            return jsonify({
                "products": products,
                "monthly_trends": monthly_trends,
                "buyer_distribution": buyer_distribution,
                "top_products": top_products,
            })

        except Exception as e:
            print(f"Error in product analytics: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({
                "products": [],
                "monthly_trends": [],
                "buyer_distribution": {},
                "top_products": [],
                "error": str(e)
            })
        finally:
            cur.close()
            conn.close()

    def get_products_for_env(cur, client_id, env, start_date, end_date, product_name=None):
        """Helper function to get products for a specific environment"""
        # Base query parameters
        params = [client_id, env]
        
        # Date condition
        date_condition = ""
        if start_date and end_date:
            date_condition = "AND COALESCE((invoice_data::jsonb->>'invoiceDate')::date, DATE(created_at)) BETWEEN %s AND %s"
            params.extend([start_date, end_date])
        
        # Product name filter
        product_filter = ""
        if product_name:
            product_filter = """
            AND (
                jsonb_array_elements(
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                            invoice_data::jsonb ? 'items' AND
                            jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                        THEN invoice_data::jsonb->'items'
                        ELSE '[]'::jsonb
                    END
                )->>'productDescription' ILIKE %s
            )
            """
            params.append(f"%{product_name}%")
        
        # Execute the main product query
        query = f"""
        WITH invoice_items AS (
                SELECT 
                    inv.id,
                    inv.created_at,
                    item->>'productDescription' as product_description,
                    (item->>'quantity')::numeric as quantity,
                    (item->>'valueSalesExcludingST')::numeric as value_excl,
                    (item->>'salesTaxApplicable')::numeric as tax,
                    (item->>'totalValues')::numeric as total_value,
                    TO_CHAR(COALESCE((inv.invoice_data::jsonb->>'invoiceDate')::timestamp, inv.created_at), 'YYYY-MM') as month
            FROM 
                invoices inv,
                jsonb_array_elements(
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                            invoice_data::jsonb ? 'items' AND
                            jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                        THEN invoice_data::jsonb->'items'
                        ELSE '[]'::jsonb
                    END
                ) as item
            WHERE 
                inv.client_id = %s 
                AND inv.env = %s 
                AND inv.status = 'Success'
                {date_condition}
                AND item->>'productDescription' IS NOT NULL
        )
        SELECT 
            product_description,
            SUM(quantity) as total_quantity,
            SUM(value_excl) as total_value,
            SUM(tax) as total_tax,
            SUM(total_value) as total_sales,
            COUNT(DISTINCT month) as months_active
        FROM 
            invoice_items
        WHERE 
            product_description IS NOT NULL
            {f"AND LOWER(product_description) LIKE LOWER(%s)" if product_name else ""}
        GROUP BY 
            product_description
        ORDER BY 
            total_sales DESC
        LIMIT 50
        """
        
        # If product_name filter is used twice, add it again to params
        if product_name and product_filter:
            params.append(f"%{product_name}%")
        
        cur.execute(query, params)
        
        products = []
        for row in cur.fetchall():
            product_name, quantity, value_excl, tax, total, months_active = row
            products.append({
                "product_name": product_name or "",
                "total_quantity": safe_float(quantity or 0),
                "total_value_excl": safe_float(value_excl or 0),
                "total_tax": safe_float(tax or 0),
                "total_sales": safe_float(total or 0),
                "months_active": int(months_active or 0),
            })
        
        return products

    def get_product_monthly_trends(cur, client_id, env, top_products, start_date=None, end_date=None):
        """Get monthly trends for top products"""
        if not top_products:
            return []
        
        # Base query parameters
        params = [client_id, env]
        
        # Date condition
        date_condition = ""
        if start_date and end_date:
            date_condition = "AND COALESCE((invoice_data::jsonb->>'invoiceDate')::date, DATE(created_at)) BETWEEN %s AND %s"
            params.extend([start_date, end_date])
        
        # Add top products as parameters
        placeholders = ", ".join(["%s"] * len(top_products))
        params.extend(top_products)
        
        query = f"""
        WITH product_months AS (
            SELECT 
                item->>'productDescription' as product_description,
                TO_CHAR(COALESCE((inv.invoice_data::jsonb->>'invoiceDate')::timestamp, inv.created_at), 'YYYY-MM') as month,
                SUM((item->>'quantity')::numeric) as quantity,
                SUM((item->>'totalValues')::numeric) as total_sales
            FROM 
                invoices inv,
                jsonb_array_elements(
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                            invoice_data::jsonb ? 'items' AND
                            jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                        THEN invoice_data::jsonb->'items'
                        ELSE '[]'::jsonb
                    END
                ) as item
            WHERE 
                inv.client_id = %s 
                AND inv.env = %s 
                AND inv.status = 'Success'
                {date_condition}
                AND item->>'productDescription' IN ({placeholders})
            GROUP BY 
                item->>'productDescription',
                month
            ORDER BY 
                month, product_description
        )
        SELECT * FROM product_months
        """
        
        cur.execute(query, params)
        
        monthly_data = {}
        for row in cur.fetchall():
            product, month, quantity, total = row
            if month not in monthly_data:
                monthly_data[month] = {"month": month}
                
            monthly_data[month][f"{product}_quantity"] = safe_float(quantity or 0)
            monthly_data[month][f"{product}_sales"] = safe_float(total or 0)
        
        # Convert to list and sort by month
        monthly_trends = list(monthly_data.values())
        monthly_trends.sort(key=lambda x: x["month"])
        
        return monthly_trends

    def get_product_buyer_distribution(cur, client_id, env, product_name=None, start_date=None, end_date=None):
        """Get buyer distribution for products"""
        # Base query parameters
        params = [client_id, env]
        
        # Date condition
        date_condition = ""
        if start_date and end_date:
            date_condition = "AND COALESCE((invoice_data::jsonb->>'invoiceDate')::date, DATE(created_at)) BETWEEN %s AND %s"
            params.extend([start_date, end_date])
        
        # Product filter
        product_filter = ""
        if product_name:
            product_filter = "AND item->>'productDescription' ILIKE %s"
            params.append(f"%{product_name}%")
        
        query = f"""
        WITH product_buyers AS (
            SELECT 
                item->>'productDescription' as product_description,
                COALESCE(
                    invoice_data::jsonb->>'buyerBusinessName',
                    invoice_data::jsonb->'buyerData'->>'buyerBusinessName',
                    'Unknown Buyer'
                ) as buyer_name,
                SUM((item->>'totalValues')::numeric) as total_sales
            FROM 
                invoices inv,
                jsonb_array_elements(
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                            invoice_data::jsonb ? 'items' AND
                            jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                        THEN invoice_data::jsonb->'items'
                        ELSE '[]'::jsonb
                    END
                ) as item
            WHERE 
                inv.client_id = %s 
                AND inv.env = %s 
                AND inv.status = 'Success'
                {date_condition}
                AND item->>'productDescription' IS NOT NULL
                {product_filter}
            GROUP BY 
                item->>'productDescription',
                buyer_name
        ),
        ranked_buyers AS (
            SELECT 
                product_description,
                buyer_name,
                total_sales,
                RANK() OVER (PARTITION BY product_description ORDER BY total_sales DESC) as buyer_rank
            FROM 
                product_buyers
            WHERE 
                buyer_name != 'Unknown Buyer'
        )
        SELECT * FROM ranked_buyers
        WHERE buyer_rank <= 5
        ORDER BY product_description, buyer_rank
        """
        
        cur.execute(query, params)
        
        buyer_distribution = {}
        for row in cur.fetchall():
            product, buyer, sales, rank = row
            if product not in buyer_distribution:
                buyer_distribution[product] = []
                
            buyer_distribution[product].append({
                "buyer_name": buyer or "Unknown",
                "total_sales": safe_float(sales or 0),
                "rank": int(rank or 0)
            })
        
        return buyer_distribution

    @app.route("/api/reports/buyer-analytics", methods=["GET"])
    def get_buyer_analytics():
        """Get buyer-specific analytics"""
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        # Use current session environment (fall back to server environment)
        env = "production"

        # Filtering parameters
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        buyer_name = request.args.get("buyer_name", "").strip()

        conn = get_db_connection()
        cur = conn.cursor()

        # Build WHERE clause and parameters
        where_conditions = ["client_id = %s", "env = %s", "status = 'Success'"]
        params = [client_id, env]

        if start_date and end_date:
            where_conditions.append("DATE(created_at) BETWEEN %s AND %s")
            params.extend([start_date, end_date])

        buyer_filter = ""
        buyer_params = []
        if buyer_name:
            buyer_filter = """
                AND (
                    (jsonb_typeof(invoice_data::jsonb) = 'object' AND
                     invoice_data::jsonb ? 'buyerBusinessName' AND
                     LOWER(invoice_data::jsonb->>'buyerBusinessName') LIKE LOWER(%s))
                    OR
                    (jsonb_typeof(invoice_data::jsonb) = 'object' AND
                     invoice_data::jsonb ? 'buyerData' AND
                     jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object' AND
                     invoice_data::jsonb->'buyerData' ? 'buyerBusinessName' AND
                     LOWER(invoice_data::jsonb->'buyerData'->>'buyerBusinessName') LIKE LOWER(%s))
                )
            """
            search_term = f"%{buyer_name}%"
            buyer_params = [search_term, search_term]

        full_params = params + buyer_params

        # Get buyer sales data - FIXED - use jsonb_array_elements_text
        cur.execute(
            f"""
            WITH buyers AS (
                SELECT
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                            CASE 
                                WHEN invoice_data::jsonb ? 'buyerBusinessName' THEN 
                                    invoice_data::jsonb->>'buyerBusinessName'
                                WHEN invoice_data::jsonb ? 'buyerData' AND 
                                     jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object' AND
                                     invoice_data::jsonb->'buyerData' ? 'buyerBusinessName' THEN
                                    invoice_data::jsonb->'buyerData'->>'buyerBusinessName'
                                ELSE 'Unknown Buyer'
                            END
                        ELSE 'Unknown Buyer'
                    END as buyer_name,
                    invoice_data,
                    id,
                    created_at
                FROM invoices
                WHERE {" AND ".join(where_conditions)}
                {buyer_filter}
            ),
            buyer_totals AS (
                SELECT
                    buyer_name,
                    SUM(val) as total_purchase
                FROM (
                    SELECT 
                        buyer_name,
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                    CASE 
                                        WHEN invoice_data::jsonb ? 'items' THEN 
                                            jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].totalValues')
                                        ELSE 
                                            '[]'::jsonb 
                                    END
                                ELSE 
                                    '[]'::jsonb 
                            END
                        )::numeric AS val
                    FROM buyers
                ) as purchase_values
                GROUP BY buyer_name
            ),
            buyer_tax AS (
                SELECT
                    buyer_name,
                    SUM(val) as total_tax
                FROM (
                    SELECT 
                        buyer_name,
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                    CASE 
                                        WHEN invoice_data::jsonb ? 'items' THEN 
                                            jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].salesTaxApplicable')
                                        ELSE 
                                            '[]'::jsonb 
                                    END
                                ELSE 
                                    '[]'::jsonb 
                            END
                        )::numeric AS val
                    FROM buyers
                ) as tax_values
                GROUP BY buyer_name
            ),
            buyer_counts AS (
                SELECT 
                    buyer_name,
                    COUNT(*) as invoice_count,
                    MIN(created_at) as first_purchase,
                    MAX(created_at) as last_purchase
                FROM buyers
                GROUP BY buyer_name
            )
            SELECT 
                bt.buyer_name,
                bt.total_purchase,
                COALESCE(btx.total_tax, 0) as total_tax,
                bc.invoice_count,
                bc.first_purchase,
                bc.last_purchase
            FROM buyer_totals bt
            JOIN buyer_counts bc ON bt.buyer_name = bc.buyer_name
            LEFT JOIN buyer_tax btx ON bt.buyer_name = btx.buyer_name
            WHERE bt.buyer_name != 'Unknown Buyer'
            ORDER BY bt.total_purchase DESC
            LIMIT 50
            """,
            full_params,
        )

        now = datetime.now()

        buyers = []
        for row in cur.fetchall():
            (
                buyer_name,
                total_purchase,
                total_tax,
                invoice_count,
                first_purchase,
                last_purchase,
            ) = row
            # Calculate days since last purchase safely
            days_since_last = 0
            try:
                # Make sure both datetimes are naive (without timezone)
                if last_purchase is not None and getattr(last_purchase, 'tzinfo', None) is not None:
                    last_purchase = last_purchase.replace(tzinfo=None)
                if last_purchase is not None:
                    days_since_last = (now - last_purchase).days
                else:
                    days_since_last = 0
            except Exception as e:
                print(f"Error calculating days since last purchase: {e}")
                days_since_last = 0

            buyers.append(
                {
                    "buyer_name": buyer_name,
                    "total_purchase": safe_float(total_purchase),
                    "total_tax": safe_float(total_tax),
                    "total_value_excl": safe_float(total_purchase) - safe_float(total_tax),
                    "invoice_count": invoice_count,
                    "average_purchase": round(safe_float(total_purchase) / invoice_count, 2)
                    if invoice_count > 0
                    else 0,
                    "first_purchase": first_purchase.isoformat() if first_purchase is not None else None,
                    "last_purchase": last_purchase.isoformat() if last_purchase is not None else None,
                    "days_since_last_purchase": days_since_last,
                }
            )

        # Get monthly trends for top 5 buyers
        top_buyers = [b["buyer_name"] for b in buyers[:5]]
        monthly_trends = []

        if top_buyers:
            monthly_params = []
            buyer_conditions = []

            for buyer in top_buyers:
                buyer_conditions.append(
                    """
                    (
                        (jsonb_typeof(invoice_data::jsonb) = 'object' AND
                         invoice_data::jsonb ? 'buyerBusinessName' AND
                         invoice_data::jsonb->>'buyerBusinessName' = %s)
                        OR
                        (jsonb_typeof(invoice_data::jsonb) = 'object' AND
                         invoice_data::jsonb ? 'buyerData' AND
                         jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object' AND
                         invoice_data::jsonb->'buyerData' ? 'buyerBusinessName' AND
                         invoice_data::jsonb->'buyerData'->>'buyerBusinessName' = %s)
                    )
                """
                )
                monthly_params.extend([buyer, buyer])

            buyer_where = " OR ".join(buyer_conditions)
            monthly_full_params = params + monthly_params

            # FIXED monthly trends query - use jsonb_array_elements_text
            cur.execute(
                f"""
                WITH buyer_months AS (
                    SELECT
                        CASE 
                            WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                CASE 
                                    WHEN invoice_data::jsonb ? 'buyerBusinessName' THEN 
                                        invoice_data::jsonb->>'buyerBusinessName'
                                    WHEN invoice_data::jsonb ? 'buyerData' AND 
                                         jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object' AND
                                         invoice_data::jsonb->'buyerData' ? 'buyerBusinessName' THEN
                                        invoice_data::jsonb->'buyerData'->>'buyerBusinessName'
                                    ELSE 'Unknown Buyer'
                                END
                            ELSE 'Unknown Buyer'
                        END as buyer_name,
                        TO_CHAR(created_at, 'YYYY-MM') as month,
                        invoice_data,
                        id
                    FROM invoices
                    WHERE {" AND ".join(where_conditions)}
                    AND ({buyer_where})
                ),
                invoice_counts AS (
                    SELECT 
                        buyer_name,
                        month,
                        COUNT(*) as invoice_count
                    FROM buyer_months
                    GROUP BY buyer_name, month
                ),
                purchase_totals AS (
                    SELECT 
                        buyer_name,
                        month,
                        SUM(val) as total_purchase
                    FROM (
                        SELECT 
                            buyer_name,
                            month,
                            jsonb_array_elements_text(
                                CASE 
                                    WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                        CASE 
                                            WHEN invoice_data::jsonb ? 'items' THEN 
                                                jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].totalValues')
                                            ELSE 
                                                '[]'::jsonb 
                                        END
                                    ELSE 
                                        '[]'::jsonb 
                                END
                            )::numeric AS val
                        FROM buyer_months
                    ) as extracted_values
                    GROUP BY buyer_name, month
                )
                SELECT 
                    ic.buyer_name,
                    ic.month,
                    pt.total_purchase,
                    ic.invoice_count
                FROM invoice_counts ic
                JOIN purchase_totals pt ON ic.buyer_name = pt.buyer_name AND ic.month = pt.month
                ORDER BY ic.month, ic.buyer_name
                """,
                monthly_full_params,
            )

            monthly_data = {}
            for row in cur.fetchall():
                buyer, month, total, count = row
                if month not in monthly_data:
                    monthly_data[month] = {"month": month}

                # Add buyer data to this month
                monthly_data[month][f"{buyer}_total"] = safe_float(total)
                monthly_data[month][f"{buyer}_count"] = count

            # Convert to list and sort by month
            monthly_trends = list(monthly_data.values())
            monthly_trends.sort(key=lambda x: x["month"])

        # Get product distribution for each buyer
        product_distribution = {}

        if buyers:
            for buyer in buyers[:10]:  # Process top 10 buyers
                buyer_name = buyer["buyer_name"]
                product_params = params + [buyer_name, buyer_name]

                cur.execute(
                    f"""
                    WITH buyer_products AS (
                        SELECT 
                            COALESCE(i.productDescription, i.description, i.productName, i.name) as productDescription,
                            SUM(i.totalValues::numeric) as total_sales,
                            SUM(i.quantity::numeric) as total_quantity
                        FROM invoices inv, 
                        jsonb_to_recordset(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                                     invoice_data::jsonb ? 'items' AND
                                     jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                                THEN invoice_data::jsonb->'items'
                                ELSE '[]'::jsonb
                            END
                        ) AS i(productDescription text, description text, productName text, name text, totalValues text, quantity text)
                        WHERE client_id = %s 
                        AND env = %s 
                        AND status = 'Success'
                        {" AND DATE(created_at) BETWEEN %s AND %s" if start_date and end_date else ""}
                        AND COALESCE(i.productDescription, i.description, i.productName, i.name) IS NOT NULL
                        AND (
                            (jsonb_typeof(invoice_data::jsonb) = 'object' AND
                             invoice_data::jsonb ? 'buyerBusinessName' AND
                             invoice_data::jsonb->>'buyerBusinessName' = %s)
                            OR
                            (jsonb_typeof(invoice_data::jsonb) = 'object' AND
                             invoice_data::jsonb ? 'buyerData' AND
                             jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object' AND
                             invoice_data::jsonb->'buyerData' ? 'buyerBusinessName' AND
                             invoice_data::jsonb->'buyerData'->>'buyerBusinessName' = %s)
                        )
                        GROUP BY COALESCE(i.productDescription, i.description, i.productName, i.name)
                        ORDER BY total_sales DESC
                        LIMIT 10
                    )
                    SELECT * FROM buyer_products
                    """,
                    product_params,
                )

                products = []
                for row in cur.fetchall():
                    product_name, total_sales, total_quantity = row
                    products.append(
                        {
                            "product_name": product_name,
                            "total_sales": safe_float(total_sales),
                            "total_quantity": safe_float(total_quantity),
                        }
                    )

                if products:
                    product_distribution[buyer_name] = products

        cur.close()
        conn.close()

        return jsonify(
            {
                "buyers": buyers,
                "monthly_trends": monthly_trends,
                "product_distribution": product_distribution,
                "top_buyers": top_buyers,
            }
        )

    @app.route("/api/reports/tax-analytics", methods=["GET"])
    def get_tax_analytics():
        """Get tax-specific analytics"""
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        # Use current session environment (fall back to server environment)
        env = "production"

        # Filtering parameters
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        conn = get_db_connection()
        cur = conn.cursor()

        # Build WHERE clause and parameters
        where_conditions = ["client_id = %s", "env = %s", "status = 'Success'"]
        params = [client_id, env]

        if start_date and end_date:
            where_conditions.append("DATE(created_at) BETWEEN %s AND %s")
            params.extend([start_date, end_date])

        # Get tax breakdown by rate - FIXED: Use jsonb_array_elements_text for proper JSON array handling
        cur.execute(
            f"""
            WITH tax_items AS (
                SELECT 
                    COALESCE(i.rate, '0%%') as tax_rate,
                    i.salesTaxApplicable::numeric as tax_amount,
                    i.valueSalesExcludingST::numeric as value_excl,
                    i.totalValues::numeric as total_value
                FROM invoices, 
                jsonb_to_recordset(
                    CASE 
                        WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                            invoice_data::jsonb ? 'items' AND
                            jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                        THEN invoice_data::jsonb->'items'
                        ELSE '[]'::jsonb
                    END
                ) AS i(rate text, salesTaxApplicable text, valueSalesExcludingST text, totalValues text)
                WHERE {" AND ".join(where_conditions)}
            )
            SELECT 
                tax_rate,
                SUM(tax_amount) as total_tax,
                SUM(value_excl) as total_value_excl,
                SUM(total_value) as total_value,
                COUNT(*) as item_count
            FROM tax_items
            GROUP BY tax_rate
            ORDER BY total_tax DESC
            """,
            params,
        )

        tax_by_rate = []
        for row in cur.fetchall():
            tax_rate, total_tax, total_value_excl, total_value, item_count = row

            # Safely coalesce NULL/None values to avoid float(None) errors
            total_tax = total_tax or 0
            total_value_excl = total_value_excl or 0
            total_value = total_value or 0
            item_count = item_count or 0

            # Extract numeric rate for calculations (e.g., "17%" -> 17)
            numeric_rate = 0
            try:
                    numeric_rate = safe_float(str(tax_rate).replace("%", ""))
            except Exception:
                numeric_rate = 0

            # Compute effective rate safely
            try:
                tv_excl = safe_float(total_value_excl)
                tt_val = safe_float(total_tax)
                effective_rate = (
                    round(tt_val * 100 / tv_excl, 2) if tv_excl > 0 else 0
                )
            except Exception:
                effective_rate = 0

            tax_by_rate.append(
                {
                    "tax_rate": tax_rate,
                    "numeric_rate": numeric_rate,
                    "total_tax": safe_float(total_tax),
                    "total_value_excl": safe_float(total_value_excl),
                    "total_value": safe_float(total_value),
                    "item_count": int(item_count),
                    "effective_rate": effective_rate,
                }
            )

        # Get monthly tax data - FIXED: Use jsonb_array_elements_text instead of UNNEST
        cur.execute(
            f"""
            WITH invoice_months AS (
                SELECT 
                    TO_CHAR(created_at, 'YYYY-MM') as month,
                    invoice_data,
                    id
                FROM invoices
                WHERE {" AND ".join(where_conditions)}
            ),
            tax_sums AS (
                SELECT 
                    month,
                    SUM(val) as total_tax
                FROM (
                    SELECT 
                        month,
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                    CASE 
                                        WHEN invoice_data::jsonb ? 'items' THEN 
                                            jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].salesTaxApplicable')
                                        ELSE 
                                            '[]'::jsonb 
                                    END
                                ELSE 
                                    '[]'::jsonb 
                            END
                        )::numeric AS val
                    FROM invoice_months
                ) as tax_values
                GROUP BY month
            ),
            value_sums AS (
                SELECT 
                    month,
                    SUM(val) as total_value_excl
                FROM (
                    SELECT 
                        month,
                        jsonb_array_elements_text(
                            CASE 
                                WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                    CASE 
                                        WHEN invoice_data::jsonb ? 'items' THEN 
                                            jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].valueSalesExcludingST')
                                        ELSE 
                                            '[]'::jsonb 
                                    END
                                ELSE 
                                    '[]'::jsonb 
                            END
                        )::numeric AS val
                    FROM invoice_months
                ) as value_values
                GROUP BY month
            ),
            invoice_counts AS (
                SELECT 
                    month,
                    COUNT(*) as invoice_count
                FROM invoice_months
                GROUP BY month
            )
            SELECT 
                ic.month,
                COALESCE(ts.total_tax, 0) as total_tax,
                COALESCE(vs.total_value_excl, 0) as total_value_excl,
                ic.invoice_count
            FROM invoice_counts ic
            LEFT JOIN tax_sums ts ON ic.month = ts.month
            LEFT JOIN value_sums vs ON ic.month = vs.month
            ORDER BY ic.month
            """,
            params,
        )

        monthly_tax = []
        for row in cur.fetchall():
            month, total_tax, total_value_excl, invoice_count = row
            # Safely coerce numeric fields
            mt_total_tax = safe_float(total_tax)
            mt_total_value_excl = safe_float(total_value_excl)
            monthly_tax.append(
                {
                    "month": month,
                    "total_tax": mt_total_tax,
                    "total_value_excl": mt_total_value_excl,
                    "invoice_count": invoice_count,
                    "effective_rate": round(mt_total_tax * 100 / mt_total_value_excl, 2)
                    if mt_total_value_excl > 0
                    else 0,
                }
            )

        # Get buyer tax contributions - FIXED

        try:
            cur.execute(
                f"""
                WITH buyers AS (
                    SELECT 
                        CASE 
                            WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                CASE 
                                    WHEN invoice_data::jsonb ? 'buyerBusinessName' THEN 
                                        invoice_data::jsonb->>'buyerBusinessName'
                                    WHEN invoice_data::jsonb ? 'buyerData' AND 
                                        jsonb_typeof(invoice_data::jsonb->'buyerData') = 'object' AND
                                        invoice_data::jsonb->'buyerData' ? 'buyerBusinessName' THEN
                                        invoice_data::jsonb->'buyerData'->>'buyerBusinessName'
                                    ELSE 'Unknown Buyer'
                                END
                            ELSE 'Unknown Buyer'
                        END as buyer_name,
                        invoice_data
                    FROM invoices
                    WHERE {" AND ".join(where_conditions)}
                ),
                buyer_tax AS (
                    SELECT
                        buyer_name,
                        SUM(val) as total_tax
                    FROM (
                        SELECT 
                            buyer_name,
                            jsonb_array_elements_text(
                                CASE 
                                    WHEN jsonb_typeof(invoice_data::jsonb) = 'object' THEN 
                                        CASE 
                                            WHEN invoice_data::jsonb ? 'items' THEN 
                                                jsonb_path_query_array(invoice_data::jsonb->'items', '$[*].salesTaxApplicable')
                                            ELSE 
                                                '[]'::jsonb 
                                        END
                                    ELSE 
                                        '[]'::jsonb 
                                END
                            )::numeric AS val
                        FROM buyers
                    ) as tax_values
                    GROUP BY buyer_name
                )
                SELECT 
                    buyer_name,
                    total_tax
                FROM buyer_tax
                WHERE buyer_name != 'Unknown Buyer'
                ORDER BY total_tax DESC
                LIMIT 10
                """,
                params,
            )

            top_tax_buyers = []
            for row in cur.fetchall():
                buyer_name, total_tax = row
                top_tax_buyers.append(
                    {"buyer_name": buyer_name, "total_tax": safe_float(total_tax)}
                )
        except Exception as e:
            print(f"Error in tax buyers query: {e}")
            top_tax_buyers = []

        # Get product tax contributions
        try:
            cur.execute(
                f"""
                WITH product_tax AS (
                    SELECT 
                        COALESCE(i.productDescription, i.description, i.productName, i.name) as productDescription,
                        SUM(i.salesTaxApplicable::numeric) as total_tax
                    FROM invoices, 
                    jsonb_to_recordset(
                        CASE 
                            WHEN jsonb_typeof(invoice_data::jsonb) = 'object' AND
                                invoice_data::jsonb ? 'items' AND
                                jsonb_typeof(invoice_data::jsonb->'items') = 'array' 
                            THEN invoice_data::jsonb->'items'
                            ELSE '[]'::jsonb
                        END
                    ) AS i(productDescription text, description text, productName text, name text, salesTaxApplicable text)
                    WHERE {" AND ".join(where_conditions)}
                    AND COALESCE(i.productDescription, i.description, i.productName, i.name) IS NOT NULL
                    GROUP BY COALESCE(i.productDescription, i.description, i.productName, i.name)
                    ORDER BY total_tax DESC
                    LIMIT 10
                )
                SELECT * FROM product_tax
                """,
                params,
            )

            top_tax_products = []
            for row in cur.fetchall():
                product_name, total_tax = row
                top_tax_products.append(
                    {"product_name": product_name, "total_tax": safe_float(total_tax)}
                )
        except Exception as e:
            print(f"Error in tax products query: {e}")
            top_tax_products = []

        cur.close()
        conn.close()

        return jsonify(
            {
                "tax_by_rate": tax_by_rate,
                "monthly_tax": monthly_tax,
                "top_tax_buyers": top_tax_buyers,
                "top_tax_products": top_tax_products,
            }
        )

    @app.route('/api/reports/summarize', methods=['POST'])
    def summarize_invoices():
        """Summarize selected invoices. Accepts JSON: { invoice_ids: [id1, id2, ...], env?: 'production'|'sandbox' }
        Returns aggregated per-product totals, per-buyer totals, invoice metadata and overall totals.
        """
        client_id = session.get('client_id')
        if not client_id:
            return jsonify({'error': 'No client ID in session'}), 401

        payload = request.get_json() or {}
        invoice_ids = payload.get('invoice_ids')
        env = payload.get('env') or 'production'

        if not invoice_ids or not isinstance(invoice_ids, list):
            return jsonify({'error': 'invoice_ids must be a non-empty list'}), 400

        # Sanitize: limit to reasonable number to avoid extremely large payloads
        if len(invoice_ids) > 200:
            return jsonify({'error': 'Too many invoices requested (max 200)'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Build placeholders and params
        placeholders = ','.join(['%s'] * len(invoice_ids))
        params = [client_id, env] + invoice_ids

        try:
            # Fetch invoices and their JSON payloads
            cur.execute(
                f"""
                SELECT id, invoice_data, fbr_response, created_at
                FROM invoices
                WHERE client_id = %s AND env = %s AND id IN ({placeholders}) AND status = 'Success'
                ORDER BY created_at
                """,
                params,
            )

            rows = cur.fetchall()

            invoices = []
            overall = {'total_value_excl': 0.0, 'total_tax': 0.0, 'total_amount': 0.0}
            product_map = {}
            buyer_map = {}

            for row in rows:
                inv_id, invoice_data_raw, fbr_raw, created_at = row
                try:
                    invoice_data = json.loads(invoice_data_raw) if isinstance(invoice_data_raw, str) else invoice_data_raw
                except Exception:
                    invoice_data = {}

                try:
                    fbr_response = json.loads(fbr_raw) if isinstance(fbr_raw, str) else fbr_raw
                except Exception:
                    fbr_response = {}

                invoice_ref = (
                    invoice_data.get('invoiceRefNo') or invoice_data.get('fbrInvoiceNumber') or fbr_response.get('invoiceNumber') or 'N/A'
                )

                buyer_name = 'Unknown Buyer'
                if 'buyerBusinessName' in invoice_data:
                    buyer_name = invoice_data.get('buyerBusinessName')
                elif 'buyerData' in invoice_data and isinstance(invoice_data.get('buyerData'), dict) and invoice_data['buyerData'].get('buyerBusinessName'):
                    buyer_name = invoice_data['buyerData'].get('buyerBusinessName')

                items = invoice_data.get('items', []) if isinstance(invoice_data, dict) else []

                inv_total_excl = 0.0
                inv_total_tax = 0.0

                simple_items = []
                for it in items:
                    qty = safe_float(it.get('quantity', 0))
                    val_excl = safe_float(it.get('valueSalesExcludingST', 0))
                    tax = safe_float(it.get('salesTaxApplicable', 0))
                    total = safe_float(it.get('totalValues', 0))
                    desc = it.get('productDescription') or it.get('description') or it.get('productName') or it.get('name') or ''

                    inv_total_excl += val_excl
                    inv_total_tax += tax

                    # Accumulate per-product
                    key = desc.strip()
                    if not key:
                        continue
                    if key not in product_map:
                        product_map[key] = {'product_name': key, 'quantity': 0.0, 'total_value_excl': 0.0, 'total_tax': 0.0, 'total_sales': 0.0}
                    product_map[key]['quantity'] += qty
                    product_map[key]['total_value_excl'] += val_excl
                    product_map[key]['total_tax'] += tax
                    product_map[key]['total_sales'] += total

                    simple_items.append({'description': key, 'quantity': qty, 'value_excl': val_excl, 'tax': tax, 'total': total})

                inv_total_amount = inv_total_excl + inv_total_tax

                # Accumulate per-buyer
                buyer = buyer_name or 'Unknown Buyer'
                if buyer not in buyer_map:
                    buyer_map[buyer] = {'buyer_name': buyer, 'invoice_count': 0, 'total_value_excl': 0.0, 'total_tax': 0.0, 'total_amount': 0.0}
                buyer_map[buyer]['invoice_count'] += 1
                buyer_map[buyer]['total_value_excl'] += inv_total_excl
                buyer_map[buyer]['total_tax'] += inv_total_tax
                buyer_map[buyer]['total_amount'] += inv_total_amount

                overall['total_value_excl'] += inv_total_excl
                overall['total_tax'] += inv_total_tax
                overall['total_amount'] += inv_total_amount

                invoices.append({'id': inv_id, 'invoice_ref': invoice_ref, 'buyer_name': buyer, 'created_at': created_at.isoformat(), 'total_value_excl': round(inv_total_excl,2), 'total_tax': round(inv_total_tax,2), 'total_amount': round(inv_total_amount,2), 'items': simple_items})

            # Convert maps to lists
            products = list(product_map.values())
            buyers = list(buyer_map.values())

            # Sort products by sales desc
            products.sort(key=lambda x: x['total_sales'], reverse=True)

            cur.close()
            conn.close()

            return jsonify({'invoices': invoices, 'products': products, 'buyers': buyers, 'overall': {'total_value_excl': round(overall['total_value_excl'],2), 'total_tax': round(overall['total_tax'],2), 'total_amount': round(overall['total_amount'],2)}})

        except Exception as e:
            print(f"Error in summarize_invoices: {e}")
            cur.close()
            conn.close()
            return jsonify({'error': 'Internal server error'}), 500
