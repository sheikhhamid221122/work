"""Reports & Analytics overview: every tile and chart on the redesigned
Reports page, for one date range, in one request.

    GET /api/reports/overview?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
    GET /downloads      the Download Center page

Same basis as the existing report endpoints in reports_routes.py so the
numbers agree with them:
  * production invoices only (reports always show real business data),
  * dated by invoiceDate, falling back to created_at,
  * sales  = sum of items' totalValues (tax inclusive),
  * tax    = sum of items' salesTaxApplicable,
  * status 'Success' counts as submitted; anything else as failed.

Each tile also carries the value for the previous period of the same length,
so the page can show a trend. Money is summed with Decimal and returned as
strings; the page only formats it.
"""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import jsonify, render_template, request, session

REPORT_ENV = "production"
TOP_N = 6
MAX_DAILY_POINTS = 62  # longer ranges are grouped by month


def _dec(value):
    try:
        text = str(value if value is not None else "0").replace(",", "").strip() or "0"
        number = Decimal(text)
        return number if number.is_finite() else Decimal("0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _money(value):
    return str(value.quantize(Decimal("0.01")))


def _as_dict(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    return {}


def _day(data, created_at):
    try:
        return datetime.strptime(str(data.get("invoiceDate") or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return created_at.date() if created_at else None


def _parse(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


def _key(text):
    return " ".join(str(text or "").split()).casefold()


def _tax_id(value):
    raw = "".join(ch for ch in str(value or "").upper() if ch.isalnum())
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits if len(digits) == 13 else raw


def _rate_label(item):
    rate = str(item.get("rate") or "").strip()
    if not rate:
        return "Other"
    try:
        number = Decimal(rate.replace("%", "").strip())
        return f"{format(number.normalize(), 'f')}%"
    except (InvalidOperation, ValueError):
        return rate  # "Exempt", "Rs.60/kg", ...


def _pct(part, whole):
    return round(float(part) / float(whole) * 100, 1) if whole else None


def add_reports_overview_routes(app, get_db_connection):

    @app.route("/downloads")
    def downloads_page():
        # Download Center. The PDFs themselves come from the existing
        # /api/reports/downloadable-invoices and download endpoints, which
        # regenerate each invoice with the client's assigned template.
        return render_template("downloads.html", active_page="downloads")

    @app.route("/api/reports/overview", methods=["GET"])
    def reports_overview():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401

        today = date.today()
        start = _parse(request.args.get("start_date"))
        end = _parse(request.args.get("end_date")) or today
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT invoice_data, status, created_at FROM invoices WHERE client_id = %s AND env = %s",
                (client_id, REPORT_ENV),
            )
            rows = []
            for raw, status, created_at in cur.fetchall():
                data = _as_dict(raw)
                day = _day(data, created_at)
                if day is None:
                    continue
                rows.append((data, str(status or "") == "Success", day))

            if start is None:  # "all time": from the first invoice
                start = min((r[2] for r in rows), default=end)
            if start > end:
                start, end = end, start
            days = (end - start).days + 1
            prev_end = start - timedelta(days=1)
            prev_start = prev_end - timedelta(days=days - 1)

            def blank():
                return {"sales": Decimal("0"), "tax": Decimal("0"), "submitted": 0, "failed": 0}

            cur_t, prev_t = blank(), blank()
            monthly = days > MAX_DAILY_POINTS
            series = {}
            cursor_day = start
            while cursor_day <= end:
                k = cursor_day.strftime("%Y-%m") if monthly else cursor_day.isoformat()
                series.setdefault(k, blank())
                cursor_day += timedelta(days=1)

            tax_by_rate = defaultdict(Decimal)
            buyers = {}
            products = {}
            first_buyer_day, first_product_day = {}, {}

            for data, ok, day in rows:
                items = [i for i in data.get("items") or [] if isinstance(i, dict)]
                sales = sum((_dec(i.get("totalValues")) for i in items), Decimal("0"))
                tax = sum((_dec(i.get("salesTaxApplicable")) for i in items), Decimal("0"))
                buyer_key = _tax_id(data.get("buyerNTNCNIC")) or _key(data.get("buyerBusinessName"))

                if ok:
                    if buyer_key and (buyer_key not in first_buyer_day or day < first_buyer_day[buyer_key]):
                        first_buyer_day[buyer_key] = day
                    for i in items:
                        pk = _key(i.get("productDescription"))
                        if pk and (pk not in first_product_day or day < first_product_day[pk]):
                            first_product_day[pk] = day

                bucket = cur_t if start <= day <= end else prev_t if prev_start <= day <= prev_end else None
                if bucket is None:
                    continue
                if not ok:
                    bucket["failed"] += 1
                    if bucket is cur_t:
                        series[day.strftime("%Y-%m") if monthly else day.isoformat()]["failed"] += 1
                    continue
                bucket["submitted"] += 1
                bucket["sales"] += sales
                bucket["tax"] += tax
                if bucket is not cur_t:
                    continue

                point = series[day.strftime("%Y-%m") if monthly else day.isoformat()]
                point["submitted"] += 1
                point["sales"] += sales
                point["tax"] += tax

                b = buyers.setdefault(buyer_key, {"name": data.get("buyerBusinessName") or "Unknown buyer",
                                                  "sales": Decimal("0"), "tax": Decimal("0"), "invoices": 0})
                b["sales"] += sales
                b["tax"] += tax
                b["invoices"] += 1
                for i in items:
                    name = " ".join(str(i.get("productDescription") or "").split()) or "Unnamed item"
                    p = products.setdefault(_key(name), {"name": name, "sales": Decimal("0"),
                                                         "qty": Decimal("0"), "lines": 0})
                    p["sales"] += _dec(i.get("totalValues"))
                    p["qty"] += _dec(i.get("quantity"))
                    p["lines"] += 1
                    line_tax = _dec(i.get("salesTaxApplicable"))
                    if line_tax:
                        tax_by_rate[_rate_label(i)] += line_tax

            # Drafts created in the range, and how many were submitted.
            drafts_total = drafts_submitted = None
            try:
                cur.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = 'invoice_drafts'"""
                )
                cols = {r[0] for r in cur.fetchall()}
                # Same "submitted" test as the Drafts page, over whichever of
                # its two markers this schema has.
                parts = []
                if "is_submitted" in cols:
                    parts.append("COALESCE(is_submitted, FALSE)")
                if "status" in cols:
                    parts.append("LOWER(COALESCE(status, '')) = 'submitted'")
                if "created_at" not in cols or not parts:
                    raise LookupError("invoice_drafts has no created_at / submitted markers")
                submitted_expr = " OR ".join(parts)
                cur.execute(
                    f"""SELECT COUNT(*), COUNT(*) FILTER (WHERE {submitted_expr})
                        FROM invoice_drafts
                        WHERE client_id = %s AND DATE(created_at) BETWEEN %s AND %s""",
                    (client_id, start, end),
                )
                drafts_total, drafts_submitted = cur.fetchone()
            except Exception as exc:
                conn.rollback()
                print(f"[reports-overview] drafts unavailable: {exc}")

            catalog = None
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM products WHERE client_id = %s AND (is_active = TRUE OR is_active IS NULL)",
                    (client_id,),
                )
                catalog = cur.fetchone()[0]
            except Exception as exc:
                conn.rollback()
                print(f"[reports-overview] catalog unavailable: {exc}")

            buyer_list = sorted(buyers.items(), key=lambda kv: kv[1]["sales"], reverse=True)
            product_list = sorted(products.values(), key=lambda p: p["sales"], reverse=True)
            total_sales = cur_t["sales"]
            repeat = sum(1 for _, b in buyer_list if b["invoices"] > 1)
            new_buyers = sum(1 for k, _ in buyer_list if start <= first_buyer_day.get(k, start) <= end)
            new_skus = sum(1 for k in products if start <= first_product_day.get(k, start) <= end)
            attempts = cur_t["submitted"] + cur_t["failed"]
            prev_attempts = prev_t["submitted"] + prev_t["failed"]

            rates = sorted(tax_by_rate.items(), key=lambda kv: kv[1], reverse=True)
            if len(rates) > 5:
                rates = rates[:4] + [("Other", sum((v for _, v in rates[4:]), Decimal("0")))]

            def tile(current, previous):
                return {"value": current, "previous": previous}

            return jsonify({
                "range": {"start": start.isoformat(), "end": end.isoformat(), "days": days,
                          "previous_start": prev_start.isoformat(), "previous_end": prev_end.isoformat(),
                          "granularity": "month" if monthly else "day"},
                "env": REPORT_ENV,
                "kpis": {
                    "sales": tile(_money(cur_t["sales"]), _money(prev_t["sales"])),
                    "tax": tile(_money(cur_t["tax"]), _money(prev_t["tax"])),
                    "sales_excl": tile(_money(cur_t["sales"] - cur_t["tax"]), _money(prev_t["sales"] - prev_t["tax"])),
                    "invoices": tile(cur_t["submitted"], prev_t["submitted"]),
                    "created": tile(attempts, prev_attempts),
                    "failed": tile(cur_t["failed"], prev_t["failed"]),
                    "success_rate": tile(_pct(cur_t["submitted"], attempts), _pct(prev_t["submitted"], prev_attempts)),
                    "avg_per_day": tile(round(cur_t["submitted"] / days, 1), round(prev_t["submitted"] / days, 1)),
                    "avg_invoice": tile(_money(cur_t["sales"] / cur_t["submitted"]) if cur_t["submitted"] else "0.00",
                                        _money(prev_t["sales"] / prev_t["submitted"]) if prev_t["submitted"] else "0.00"),
                },
                "series": [
                    {"period": k, "sales": _money(v["sales"]), "tax": _money(v["tax"]),
                     "sales_excl": _money(v["sales"] - v["tax"]), "invoices": v["submitted"], "failed": v["failed"]}
                    for k, v in sorted(series.items())
                ],
                "tax_breakdown": [{"name": n, "value": _money(v)} for n, v in rates],
                "buyers": {
                    "active": len(buyer_list),
                    "new": new_buyers,
                    "repeat_rate": _pct(repeat, len(buyer_list)),
                    "top_share": _pct(buyer_list[0][1]["sales"], total_sales) if buyer_list else None,
                    "top": [{"name": b["name"], "sales": _money(b["sales"]), "tax": _money(b["tax"]),
                             "invoices": b["invoices"]} for _, b in buyer_list[:TOP_N]],
                },
                "products": {
                    "catalog": catalog,
                    "sold": len(product_list),
                    "units": format(sum((p["qty"] for p in product_list), Decimal("0")).normalize(), "f"),
                    "new_skus": new_skus,
                    "best_seller": ({"name": product_list[0]["name"], "sales": _money(product_list[0]["sales"])}
                                    if product_list else None),
                    "top": [{"name": p["name"], "sales": _money(p["sales"]), "qty": format(p["qty"].normalize(), "f"),
                             "lines": p["lines"]} for p in product_list[:TOP_N]],
                    "top_units": [{"name": p["name"], "qty": format(p["qty"].normalize(), "f")}
                                  for p in sorted(product_list, key=lambda p: p["qty"], reverse=True)[:TOP_N]],
                },
                "drafts": {"created": drafts_total, "submitted": drafts_submitted,
                           "conversion": _pct(drafts_submitted or 0, drafts_total) if drafts_total else None},
            })
        finally:
            cur.close()
            conn.close()
