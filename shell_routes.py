"""Routes behind the app shell (top bar + sidebar) that every page shares.

- /api/search           -- the top-bar search: invoices, buyers, products
- /api/notifications    -- the bell: latest notifications + unread count
- /api/notifications/read
- /settings -- placeholder page until the real one exists

Everything here is read-only apart from marking notifications read, and every
query is scoped to the logged-in client. Authentication is enforced app-wide by
the before_request hook in app.py.
"""

import json

from flask import jsonify, render_template, request, session

SEARCH_SECTION_LIMIT = 6

# Postgres SQLSTATE for "relation does not exist": the notifications
# migration has not been applied yet.
UNDEFINED_TABLE = "42P01"


def _like(term):
    """ILIKE pattern for a free-text term, with LIKE wildcards escaped."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _as_dict(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _pgcode(exc):
    return getattr(exc, "pgcode", None)


def add_shell_routes(app, get_db_connection):

    # ------------------------------------------------------------------ search

    def _search_invoices(cur, client_id, env, term):
        # The JSON is matched as text first so the database does the narrowing;
        # Python then checks the actual fields, because a text match can also
        # hit a JSON key name or an unrelated field.
        cur.execute(
            """
            SELECT id, invoice_data, fbr_response, status, created_at
            FROM invoices
            WHERE client_id = %s AND env = %s
              AND (invoice_data::text ILIKE %s OR fbr_response::text ILIKE %s)
            ORDER BY created_at DESC
            LIMIT 200
            """,
            (client_id, env, _like(term), _like(term)),
        )
        needle = term.lower()
        results = []
        for invoice_id, data_raw, fbr_raw, status, created_at in cur.fetchall():
            data = _as_dict(data_raw)
            fbr = _as_dict(fbr_raw)
            number = data.get("fbrInvoiceNumber") or fbr.get("invoiceNumber") or ""
            ref_no = data.get("invoiceRefNo") or ""
            buyer = data.get("buyerBusinessName") or ""
            buyer_ntn = data.get("buyerNTNCNIC") or ""
            haystack = " ".join(str(v) for v in (number, ref_no, buyer, buyer_ntn)).lower()
            if needle not in haystack:
                continue
            results.append({
                "id": invoice_id,
                "number": number or "N/A",
                "refNo": ref_no,
                "buyer": buyer,
                "date": data.get("invoiceDate") or (created_at.strftime("%Y-%m-%d") if created_at else ""),
                "success": str(status or "").lower() == "success",
            })
            if len(results) >= SEARCH_SECTION_LIMIT:
                break
        return results

    def _search_buyers(cur, client_id, term):
        pattern = _like(term)
        cur.execute(
            """
            SELECT id, business_name, ntn_cnic, province, buyer_code
            FROM buyers
            WHERE client_id = %s
              AND (business_name ILIKE %s
                   OR COALESCE(ntn_cnic::text, '') ILIKE %s
                   OR COALESCE(buyer_code::text, '') ILIKE %s)
            ORDER BY business_name
            LIMIT %s
            """,
            (client_id, pattern, pattern, pattern, SEARCH_SECTION_LIMIT),
        )
        return [
            {"id": r[0], "name": r[1] or "", "ntn": r[2] or "", "province": r[3] or "", "code": r[4] or ""}
            for r in cur.fetchall()
        ]

    def _search_products(cur, client_id, term):
        pattern = _like(term)
        cur.execute(
            """
            SELECT id, description, hs_code, uom
            FROM products
            WHERE client_id = %s
              AND (is_active = TRUE OR is_active IS NULL)
              AND (description ILIKE %s OR COALESCE(hs_code::text, '') ILIKE %s)
            ORDER BY description
            LIMIT %s
            """,
            (client_id, pattern, pattern, SEARCH_SECTION_LIMIT),
        )
        return [
            {"id": r[0], "description": r[1] or "", "hsCode": r[2] or "", "uom": r[3] or ""}
            for r in cur.fetchall()
        ]

    @app.route("/api/search", methods=["GET"])
    def shell_search():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        term = (request.args.get("q") or "").strip()[:100]
        empty = {"invoices": [], "buyers": [], "products": []}
        if len(term) < 2:
            return jsonify(empty)

        env = session.get("env", "sandbox")
        results = dict(empty)
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Each section stands alone: one failing (say, a column missing on
            # an older schema) must not blank the other two. A failed statement
            # aborts the transaction, so roll back before the next one.
            for key, run in (
                ("invoices", lambda: _search_invoices(cur, client_id, env, term)),
                ("buyers", lambda: _search_buyers(cur, client_id, term)),
                ("products", lambda: _search_products(cur, client_id, term)),
            ):
                try:
                    results[key] = run()
                except Exception as exc:
                    conn.rollback()
                    print(f"[search] {key} lookup failed: {exc}")
            return jsonify(results)
        finally:
            cur.close()
            conn.close()

    # ----------------------------------------------------------- notifications

    # A notification with client_id NULL goes to every client. Reads are
    # tracked per client in notification_reads, so a broadcast can be read by
    # one client and still be unread for the rest.
    VISIBLE = """
        (n.client_id IS NULL OR n.client_id = %s)
        AND (n.expires_at IS NULL OR n.expires_at > NOW())
    """

    @app.route("/api/notifications", methods=["GET"])
    def shell_notifications():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                f"""
                SELECT n.id, n.title, n.message, n.link, n.level, n.created_at,
                       (r.notification_id IS NOT NULL) AS is_read
                FROM notifications n
                LEFT JOIN notification_reads r
                       ON r.notification_id = n.id AND r.client_id = %s
                WHERE {VISIBLE}
                ORDER BY n.created_at DESC
                LIMIT 20
                """,
                (client_id, client_id),
            )
            items = [
                {
                    "id": r[0],
                    "title": r[1] or "",
                    "message": r[2] or "",
                    "link": r[3] or "",
                    "level": r[4] or "info",
                    "created_at": r[5].isoformat() if r[5] else "",
                    "read": bool(r[6]),
                }
                for r in cur.fetchall()
            ]
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM notifications n
                WHERE {VISIBLE}
                  AND NOT EXISTS (
                      SELECT 1 FROM notification_reads r
                      WHERE r.notification_id = n.id AND r.client_id = %s)
                """,
                (client_id, client_id),
            )
            unread = cur.fetchone()[0]
            return jsonify({"unread": unread, "items": items})
        except Exception as exc:
            conn.rollback()
            if _pgcode(exc) == UNDEFINED_TABLE:
                # Deploy ahead of its migration: an empty bell, not an error.
                return jsonify({"unread": 0, "items": [], "setup_required": True})
            print(f"[notifications] load failed: {exc}")
            return jsonify({"error": "Failed to load notifications"}), 500
        finally:
            cur.close()
            conn.close()

    @app.route("/api/notifications/read", methods=["POST"])
    def shell_notifications_read():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        payload = request.get_json(silent=True) or {}
        ids = [i for i in (payload.get("ids") or []) if isinstance(i, int)]
        mark_all = payload.get("all") is True
        if not ids and not mark_all:
            return jsonify({"success": True, "marked": 0})

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            query = f"""
                INSERT INTO notification_reads (notification_id, client_id)
                SELECT n.id, %s FROM notifications n
                WHERE {VISIBLE}
                {"" if mark_all else "AND n.id = ANY(%s)"}
                ON CONFLICT DO NOTHING
            """
            params = [client_id, client_id] + ([] if mark_all else [ids])
            cur.execute(query, params)
            marked = cur.rowcount
            conn.commit()
            return jsonify({"success": True, "marked": marked})
        except Exception as exc:
            conn.rollback()
            if _pgcode(exc) == UNDEFINED_TABLE:
                return jsonify({"success": True, "marked": 0, "setup_required": True})
            print(f"[notifications] mark read failed: {exc}")
            return jsonify({"error": "Failed to update notifications"}), 500
        finally:
            cur.close()
            conn.close()

    # --------------------------------------------------------- placeholder pages

    PLACEHOLDERS = {
        "settings": ("Settings", "fa-gear",
                     "Account and workspace settings."),
    }

    def _placeholder(page):
        title, icon, blurb = PLACEHOLDERS[page]
        return render_template(
            "coming-soon.html",
            active_page=page,
            page_title=title,
            page_icon=icon,
            page_blurb=blurb,
            query=(request.args.get("q") or "").strip()[:100],
        )

    @app.route("/settings")
    def settings_page():
        return _placeholder("settings")
