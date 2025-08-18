from flask import request, jsonify, session, redirect, url_for, render_template
import json
from datetime import datetime, timedelta


def add_draft_invoice_routes(app, get_db_connection, get_env):
    @app.route("/draft-invoices.html")
    def draft_invoices_html():
        if "user_id" not in session:
            return redirect(url_for("index"))
        return render_template("draft-invoices.html")

    @app.route("/api/draft-invoices", methods=["GET"])
    def get_draft_invoices():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        env = get_env()
        filter_env = request.args.get("filter_env", "all")
        filter_date = request.args.get("filter_date", "all")
        search = request.args.get("search", "").strip().lower()

        conn = get_db_connection()
        cur = conn.cursor()

        # Build query conditions based on filters
        conditions = ["client_id = %s"]
        params = [client_id]

        # Environment filter - FIX: Simplify the condition logic
        if filter_env != "all":
            # Only filter by original_env, which indicates where the draft was created
            conditions.append("original_env = %s")
            params.append(filter_env)

        # Date filter
        if filter_date != "all":
            if filter_date == "today":
                conditions.append("DATE(created_at) = CURRENT_DATE")
            elif filter_date == "week":
                conditions.append("created_at >= CURRENT_DATE - INTERVAL '7 days'")
            elif filter_date == "month":
                conditions.append("created_at >= CURRENT_DATE - INTERVAL '30 days'")

        # Build the query
        query = f"""
            SELECT id, client_id, env, original_env, seller_profile_id, buyer_id, 
                invoice_data, status, created_at, updated_at, title, last_accessed, is_submitted
            FROM invoice_drafts
            WHERE {" AND ".join(conditions)}
            ORDER BY updated_at DESC
        """

        cur.execute(query, params)

        # Convert to list of dictionaries
        columns = [desc[0] for desc in cur.description]
        drafts = []

        for row in cur.fetchall():
            draft = dict(zip(columns, row))

            # Filter by search query if provided
            if search:
                # Parse invoice data if it's a string
                invoice_data = draft["invoice_data"]
                if isinstance(invoice_data, str):
                    try:
                        invoice_data = json.loads(invoice_data)
                    except:
                        invoice_data = {}

                # Check if search matches title or buyer name
                title = (draft.get("title") or "").lower()
                buyer_name = (invoice_data.get("buyerData", {}).get("buyerBusinessName", "")).lower()

                if search not in title and search not in buyer_name:
                    continue

            # Convert datetime objects to strings
            for key, value in draft.items():
                if isinstance(value, datetime):
                    draft[key] = value.isoformat()

            drafts.append(draft)

        cur.close()
        conn.close()

        return jsonify(drafts)

    @app.route("/api/draft-invoices/update-title", methods=["POST"])
    def update_draft_title():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json()
        draft_id = data.get("draft_id")
        new_title = data.get("title")

        if not draft_id or not new_title:
            return jsonify({"error": "Missing draft ID or title"}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Verify ownership
        cur.execute(
            "SELECT id FROM invoice_drafts WHERE id = %s AND client_id = %s",
            (draft_id, client_id),
        )

        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": "Draft not found or access denied"}), 404

        # Update title
        cur.execute(
            """
            UPDATE invoice_drafts 
            SET title = %s, updated_at = NOW()
            WHERE id = %s AND client_id = %s
            """,
            (new_title, draft_id, client_id),
        )

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True, "message": "Draft title updated successfully"})

    @app.route("/api/draft-invoices/delete", methods=["POST"])
    def delete_draft_invoice():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json()
        draft_id = data.get("draft_id")

        if not draft_id:
            return jsonify({"error": "Missing draft ID"}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Verify ownership
        cur.execute(
            "SELECT id FROM invoice_drafts WHERE id = %s AND client_id = %s",
            (draft_id, client_id),
        )

        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": "Draft not found or access denied"}), 404

        # Delete the draft
        cur.execute(
            "DELETE FROM invoice_drafts WHERE id = %s AND client_id = %s",
            (draft_id, client_id),
        )

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True, "message": "Draft deleted successfully"})

    @app.route("/api/draft-invoices/<int:draft_id>", methods=["GET"])
    def get_draft_invoice(draft_id):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        conn = get_db_connection()
        cur = conn.cursor()

        # Get the draft with client verification
        cur.execute(
            """
            SELECT id, client_id, env, original_env, seller_profile_id, buyer_id, 
                   invoice_data, status, created_at, updated_at, title
            FROM invoice_drafts
            WHERE id = %s AND client_id = %s
            """,
            (draft_id, client_id),
        )

        row = cur.fetchone()

        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "Draft not found or access denied"}), 404

        # Convert to dictionary
        columns = [desc[0] for desc in cur.description]
        draft = dict(zip(columns, row))

        # Update last_accessed timestamp
        cur.execute(
            """
            UPDATE invoice_drafts
            SET last_accessed = NOW()
            WHERE id = %s
            """,
            (draft_id,),
        )

        conn.commit()
        cur.close()
        conn.close()

        # Convert datetime objects to strings
        for key, value in draft.items():
            if isinstance(value, datetime):
                draft[key] = value.isoformat()

        return jsonify(draft)
