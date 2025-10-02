from flask import request, jsonify, session, redirect, url_for, render_template
import json
from datetime import datetime, timedelta
from decimal import Decimal


def _normalize_json(value):
    """Recursively convert database types (e.g., Decimal) into JSON-friendly primitives."""
    if isinstance(value, Decimal):
        # Convert to float while preserving numeric meaning; fallback to 0.0 if NaN
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalize_json(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_json(item) for item in value]
    return value


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

        filter_env = request.args.get("filter_env", "all")
        filter_submitted = request.args.get("filter_submitted", "not_submitted")
        filter_date = request.args.get("filter_date", "all")
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        search = request.args.get("search", "").strip().lower()

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Discover available columns for compatibility
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'invoice_drafts'
                """
            )
            available_columns = {row[0] for row in cur.fetchall()}
            has_original_env = "original_env" in available_columns
            has_is_submitted = "is_submitted" in available_columns
            has_seller_profile_id = "seller_profile_id" in available_columns
            has_buyer_id = "buyer_id" in available_columns
            has_last_accessed = "last_accessed" in available_columns

            # Build robust SQL conditions (client/date/env only)
            conditions = ["client_id = %s"]
            params = [client_id]

            if filter_env != "all":
                env_col = "original_env" if has_original_env else "env"
                conditions.append(f"{env_col} = %s")
                params.append(filter_env)

            if start_date or end_date:
                if start_date and not end_date:
                    end_date = start_date
                if end_date and not start_date:
                    start_date = end_date
                conditions.append("DATE(created_at) BETWEEN %s AND %s")
                params.extend([start_date, end_date])
            elif filter_date != "all":
                if filter_date == "today":
                    conditions.append("DATE(created_at) = CURRENT_DATE")
                elif filter_date == "week":
                    conditions.append("created_at >= CURRENT_DATE - INTERVAL '7 days'")
                elif filter_date == "month":
                    conditions.append("created_at >= CURRENT_DATE - INTERVAL '30 days'")

            select_exprs = [
                "id",
                "client_id",
                "env",
                "invoice_data",
                "status",
                "created_at",
                "updated_at",
                "title",
            ]

            # Fallback/aliases for optional columns
            if has_original_env:
                select_exprs.append("original_env")
            else:
                select_exprs.append("env AS original_env")

            if has_seller_profile_id:
                select_exprs.append("seller_profile_id")
            else:
                select_exprs.append("NULL::INTEGER AS seller_profile_id")

            if has_buyer_id:
                select_exprs.append("buyer_id")
            else:
                select_exprs.append("NULL::INTEGER AS buyer_id")

            if has_is_submitted:
                select_exprs.append("COALESCE(is_submitted, FALSE) AS is_submitted")
            else:
                select_exprs.append("FALSE AS is_submitted")

            if has_last_accessed:
                select_exprs.append("last_accessed")
            else:
                select_exprs.append("updated_at AS last_accessed")

            query = f"""
                SELECT {', '.join(select_exprs)}
                FROM invoice_drafts
                WHERE {' AND '.join(conditions)}
                ORDER BY updated_at DESC
            """

            cur.execute(query, params)

            columns = [desc[0] for desc in cur.description]
            drafts = []

            for row in cur.fetchall():
                draft = dict(zip(columns, row))

                # Parse invoice_data if string
                inv = draft.get("invoice_data")
                if isinstance(inv, str):
                    try:
                        inv = json.loads(inv)
                    except Exception:
                        inv = {}
                draft["invoice_data"] = inv or {}

                # Apply search filter (title or buyer name)
                if search:
                    title = (draft.get("title") or "").lower()
                    buyer_name = (
                        (draft["invoice_data"].get("buyerData", {}) or {}).get("buyerBusinessName", "")
                        or draft["invoice_data"].get("buyerBusinessName", "")
                    ).lower()
                    if search not in title and search not in buyer_name:
                        continue

                # Apply submitted filter in Python for robustness
                if filter_submitted != "all":
                    is_sub = bool(draft.get("is_submitted") or False)
                    status = (draft.get("status") or "").lower()
                    if filter_submitted == "submitted":
                        if not (is_sub or status == "submitted"):
                            continue
                    else:  # not_submitted
                        if (is_sub or status == "submitted"):
                            continue

                # Normalize datetime fields
                for k in ("created_at", "updated_at", "last_accessed"):
                    if isinstance(draft.get(k), datetime):
                        draft[k] = draft[k].isoformat()

                drafts.append(draft)

            return jsonify(drafts)
        except Exception as e:
            conn.rollback()
            return jsonify({"error": f"Failed to load Draft invoices: {str(e)}"}), 500
        finally:
            cur.close()
            conn.close()

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

        conn = None
        cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'invoice_drafts'
                """
            )
            available_columns = {row[0] for row in cur.fetchall()}

            has_original_env = "original_env" in available_columns
            has_seller_profile_id = "seller_profile_id" in available_columns
            has_buyer_id = "buyer_id" in available_columns
            has_is_submitted = "is_submitted" in available_columns
            has_last_accessed = "last_accessed" in available_columns

            select_exprs = [
                "id",
                "client_id",
                "env",
                "invoice_data",
                "status",
                "created_at",
                "updated_at",
                "title",
            ]

            if has_original_env:
                select_exprs.append("original_env")
            else:
                select_exprs.append("env AS original_env")

            if has_seller_profile_id:
                select_exprs.append("seller_profile_id")
            else:
                select_exprs.append("NULL::INTEGER AS seller_profile_id")

            if has_buyer_id:
                select_exprs.append("buyer_id")
            else:
                select_exprs.append("NULL::INTEGER AS buyer_id")

            if has_is_submitted:
                select_exprs.append("COALESCE(is_submitted, FALSE) AS is_submitted")
            else:
                select_exprs.append("FALSE AS is_submitted")

            if has_last_accessed:
                select_exprs.append("last_accessed")

            query = f"""
                SELECT {', '.join(select_exprs)}
                FROM invoice_drafts
                WHERE id = %s AND client_id = %s
            """

            cur.execute(query, (draft_id, client_id))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": "Draft not found or access denied"}), 404

            columns = [desc[0] for desc in cur.description]
            draft = dict(zip(columns, row))

            invoice_data = draft.get("invoice_data") or {}
            if isinstance(invoice_data, str):
                try:
                    invoice_data = json.loads(invoice_data)
                except Exception:
                    invoice_data = {}

            invoice_data = _normalize_json(invoice_data)

            draft["invoice_data"] = invoice_data
            draft["original_env"] = draft.get("original_env") or draft.get("env")
            draft["is_submitted"] = bool(draft.get("is_submitted", False))

            for key, value in list(draft.items()):
                if isinstance(value, datetime):
                    draft[key] = value.isoformat()
                elif isinstance(value, Decimal):
                    draft[key] = float(value)
                elif isinstance(value, (dict, list, tuple, set)):
                    draft[key] = _normalize_json(value)

            if has_last_accessed:
                try:
                    cur.execute(
                        """
                        UPDATE invoice_drafts
                        SET last_accessed = NOW()
                        WHERE id = %s
                        """,
                        (draft_id,),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()

            return jsonify(draft)
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return jsonify({"error": f"Failed to load draft invoice: {str(e)}"}), 500
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    @app.route("/api/draft-invoices/mark-submitted", methods=["POST"])
    def mark_draft_submitted():
        """Mark a draft as submitted/used.

        Note: Only production submissions set is_submitted=True (drives the UI "Submitted" tag).
        Sandbox submissions will NOT set is_submitted, preserving the tag's meaning.

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
            # Determine environment to control submission semantics
            env = get_env()

            if env == "production":
                # Only in production we flag as submitted
                cur.execute(
                    """
                    UPDATE invoice_drafts
                    SET status = %s, is_submitted = TRUE, updated_at = NOW()
                    WHERE id = %s AND client_id = %s
                    """,
                    ("submitted", draft_id, client_id),
                )
                message = "Draft marked as submitted (production)"
            else:
                # In sandbox, do not set is_submitted so the UI tag won't appear
                # Optionally record a neutral status for traceability
                cur.execute(
                    """
                    UPDATE invoice_drafts
                    SET status = %s, updated_at = NOW()
                    WHERE id = %s AND client_id = %s
                    """,
                    ("sandbox_submitted", draft_id, client_id),
                )
                message = "Draft processed in sandbox (not marked as submitted)"

            conn.commit()
            return jsonify({"success": True, "message": message})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            cur.close()
            conn.close()

    @app.route("/api/draft-invoices/unsubmit", methods=["POST"])
    def unsubmit_draft():
        """Revert a draft's submitted state so it appears as not submitted in the UI.

        Body: { draft_id: int }
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        data = request.get_json() or {}
        draft_id = data.get("draft_id")
        if not draft_id:
            return jsonify({"error": "Missing draft_id"}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Verify ownership
            cur.execute(
                "SELECT id FROM invoice_drafts WHERE id = %s AND client_id = %s",
                (draft_id, client_id),
            )
            if not cur.fetchone():
                return jsonify({"error": "Draft not found or access denied"}), 404

            # Detect is_submitted column
            cur.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'invoice_drafts' AND column_name = 'is_submitted'
                """
            )
            _has_is_submitted = cur.fetchone()[0] > 0

            if _has_is_submitted:
                cur.execute(
                    """
                    UPDATE invoice_drafts
                    SET is_submitted = FALSE, status = %s, updated_at = NOW()
                    WHERE id = %s AND client_id = %s
                    """,
                    ("not_submitted", draft_id, client_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE invoice_drafts
                    SET status = %s, updated_at = NOW()
                    WHERE id = %s AND client_id = %s
                    """,
                    ("not_submitted", draft_id, client_id),
                )
            conn.commit()
            return jsonify({"success": True, "message": "Draft marked as not submitted"})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            cur.close()
            conn.close()
