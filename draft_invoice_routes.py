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
        # New: support filtering by submission status: all | submitted | not_submitted
        filter_submitted = request.args.get("filter_submitted", "all")
        filter_date = request.args.get("filter_date", "all")
        search = request.args.get("search", "").strip().lower()

        try:
            conn = get_db_connection()
            cur = conn.cursor()

            # Build query conditions based on filters
            conditions = ["client_id = %s"]
            params = [client_id]

            # Environment filter
            if filter_env != "all":
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

            # Detect whether `is_submitted` column exists to remain backwards-compatible
            try:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'invoice_drafts' AND column_name = 'is_submitted'
                    """
                )
                has_is_submitted = cur.fetchone()[0] > 0
            except Exception:
                # If information_schema is not accessible for any reason, assume column missing
                has_is_submitted = False

            # Add is_submitted filter to SQL conditions if the column exists and filter is set
            if filter_submitted != "all":
                if has_is_submitted:
                    # Use the boolean column when available
                    is_submitted_value = filter_submitted == "submitted"
                    conditions.append("is_submitted = %s")
                    params.append(is_submitted_value)
                else:
                    # Fallback for older schemas: filter by status field
                    # Treat status == 'submitted' as submitted; everything else as not submitted
                    if filter_submitted == "submitted":
                        conditions.append("status = %s")
                        params.append("submitted")
                    else:
                        # not_submitted: include rows where status is NULL or not 'submitted'
                        conditions.append("(status IS NULL OR status != %s)")
                        params.append("submitted")

            # Build the query with or without is_submitted
            select_cols = (
                "id, client_id, env, original_env, seller_profile_id, buyer_id, invoice_data, status, is_submitted, created_at, updated_at, title, last_accessed"
                if has_is_submitted
                else "id, client_id, env, original_env, seller_profile_id, buyer_id, invoice_data, status, created_at, updated_at, title, last_accessed"
            )

            # Build the query
            query = f"""
                SELECT {select_cols}
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

                # Normalize is_submitted key: some older DBs won't have the column
                # so treat missing/None as False.
                if "is_submitted" not in draft or draft.get("is_submitted") is None:
                    draft["is_submitted"] = False

                # Parse invoice_data JSON safely for searching and display
                invoice_data = draft.get("invoice_data") or {}
                if isinstance(invoice_data, str):
                    try:
                        invoice_data = json.loads(invoice_data)
                    except Exception:
                        invoice_data = {}

                # Filter by search query if provided
                if search:
                    title = (draft.get("title") or "").lower()
                    buyer_name = (
                        (invoice_data.get("buyerData", {}) or {}).get("buyerBusinessName", "")
                        or invoice_data.get("buyerBusinessName", "")
                    ).lower()

                    if search not in title and search not in buyer_name:
                        continue

                # Convert datetime objects to ISO strings for JSON
                for key, value in draft.items():
                    if isinstance(value, datetime):
                        draft[key] = value.isoformat()

                drafts.append(draft)

            cur.close()
            conn.close()

            return jsonify(drafts)
        except Exception as e:
            # Return proper error response with the exception details
            return jsonify({"error": f"Failed to load Draft invoices: {str(e)}"}), 500

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

        env = get_env()
        filter_env = request.args.get("filter_env", "all")
        filter_submitted = request.args.get("filter_submitted", "all")
        filter_date = request.args.get("filter_date", "all")
        search = request.args.get("search", "").strip().lower()
        
        print(f"API request received - filter_submitted: {filter_submitted}")

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

        # Try to fetch is_submitted if present in table (backwards-compatible)
        try:
            cur.execute(
                "SELECT is_submitted FROM invoice_drafts WHERE id = %s",
                (draft_id,),
            )
            srow = cur.fetchone()
            if srow:
                draft["is_submitted"] = srow[0]
            else:
                draft["is_submitted"] = False
        except Exception:
            draft["is_submitted"] = False

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

    @app.route("/api/draft-invoices/mark-submitted", methods=["POST"])
    def mark_draft_submitted():
        """Mark a draft as submitted/used in production so UI can hide or label it.

        Body: { draft_id: int, action: 'mark'|'delete' }
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json() or {}
        draft_id = data.get("draft_id")
        action = data.get("action", "mark")

        if not draft_id:
            return jsonify({"error": "Missing draft_id"}), 400

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

        if action == "delete":
            cur.execute(
                "DELETE FROM invoice_drafts WHERE id = %s AND client_id = %s",
                (draft_id, client_id),
            )
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({"success": True, "message": "Draft deleted"})

        # Default: mark as submitted
        try:
            cur.execute(
                """
                UPDATE invoice_drafts
                SET status = %s, is_submitted = TRUE, updated_at = NOW()
                WHERE id = %s AND client_id = %s
                """,
                ("submitted", draft_id, client_id),
            )
            conn.commit()
            return jsonify({"success": True, "message": "Draft marked as submitted"})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            cur.close()
            conn.close()
