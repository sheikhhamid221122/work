"""Routes for the invoice template picker, letterhead and template builder.

Everything a client can change about how their invoices look is here. The one
thing they cannot change is the FBR compliance block -- see invoice_spec.validate(),
which guarantees every saved custom template still carries the QR code, the FBR
Digital Invoicing logo and the FBR invoice number.
"""

from __future__ import annotations

import base64
import io
import json
import os
import uuid
from functools import wraps

from flask import jsonify, redirect, render_template, request, send_file, session, url_for

from compliance import apply_compliance, get_profile, meta_rows, resolve_field

import invoice_spec
import invoice_templates
import letterhead_import

# Columns this screen owns. Kept in one place so the SELECT, the UPDATE and the
# allow-list below cannot drift apart.
# Whether clients get the Invoice Appearance and designer screens.
#
# Off. Decided 2026-09-24: with clients onboarded by hand, a bespoke template
# per client produces a better invoice than any self-serve builder, and a
# builder most clients would never open is a support surface for no gain.
#
# What stays behind it, deliberately:
#   * the letterhead reservation and full-bleed rendering, which is how a new
#     client's masthead gets onto their invoices without hand-coding it;
#   * the thirteen layouts and the theme, reachable by setting clients.tpl_*
#     directly, so a bespoke template can start from one instead of a blank
#     file;
#   * the tpl_* columns, which are all nullable and mean "behave exactly as
#     before" when NULL -- which is every client today.
#
# Set to True to put the screens back; nothing else has to change.
EXPOSE_TEMPLATE_UI = False

SETTING_COLUMNS = (
    "tpl_template", "tpl_accent_color", "tpl_font", "tpl_density",
    "tpl_letterhead_enabled", "tpl_letterhead_mm", "tpl_letterhead_url",
    "tpl_letterhead_fullbleed", "tpl_seller_display",
    "tpl_logo_mm", "tpl_qr_mm", "tpl_custom_spec", "tpl_custom_name",
)

ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_LETTERHEAD_BYTES = 2 * 1024 * 1024   # 2MB, same ceiling the market uses


def _sample_invoice(seller=None):
    """A representative invoice for previews.

    The buyer and the figures are obviously-fake and fixed, so a preview never
    shows another client's data and the gallery looks the same every time. The
    *seller* block is the client's own registered details when we have them
    (see `_seller_defaults`), because a designer that previews "Your Company"
    while the real invoice prints the client's letterhead name is a designer
    the client cannot trust.

    Every optional identifier is populated on purpose. A row the client's
    configuration is able to print must be visible here, or they will design a
    layout with no room for a field their real invoices carry.
    """
    seller = seller or {}
    items = []
    for i in range(5):
        excl = 25000.0 + i * 1310.5
        tax = round(excl * 0.18, 2)
        items.append({
            "productDescription": f"Sample Product {chr(65 + i)} - 58 inch",
            "hsCode": "5208.5210", "product_code": f"PRD-{1000 + i}",
            "quantity": 10 + i, "unitrate": round(excl / (10 + i), 2), "rate": "18%",
            "uoM": "Numbers, pieces, units",
            "valueSalesExcludingST": excl, "salesTaxApplicable": tax,
            "totalValues": round(excl + tax, 2),
            # The remaining DI API item fields. Zero is the honest sample for
            # most of them -- they only carry a figure for specific sale types
            # -- but they must exist so a column the client switches on is not
            # blank in the preview and populated in production.
            "fixedNotifiedValueOrRetailPrice": 0.0,
            "salesTaxWithheldAtSource": 0.0,
            "extraTax": 0.0, "furtherTax": 0.0, "furtherTaxAmount": 0,
            "fedPayable": 0.0, "discount": 0.0,
            "saleType": "Goods at standard rate (default)",
            "sroScheduleNo": "", "sroItemSerialNo": "",
        })
    excl_total = sum(i["valueSalesExcludingST"] for i in items)
    tax_total = sum(i["salesTaxApplicable"] for i in items)
    return {
        # --- seller: the client's own details when available ---------------
        "sellerBusinessName": seller.get("business_name")
            or session.get("name") or "Your Company",
        "sellerLegalName": seller.get("business_name")
            or session.get("name") or "Your Company (Private) Limited",
        "sellerAddress": seller.get("address")
            or "Your registered business address, City, Province",
        "sellerProvince": seller.get("province") or "Punjab",
        "sellerNTNCNIC": seller.get("ntn_cnic") or "0000000-0",
        "sellerSTRN": seller.get("strn") or "00-00-0000-000-00",

        # --- buyer: always fake -------------------------------------------
        "buyerBusinessName": "Sample Buyer (Private) Limited",
        "buyerAddress": "Buyer address, City", "buyerNTNCNIC": "1111111-1",
        "buyerSTRN": "11-11-1111-111-11", "buyerRegistrationType": "Registered",
        "buyerProvince": "Sindh",

        # --- document identifiers -----------------------------------------
        "invoiceType": "Sale Invoice",
        "invoiceRefNo": "0001", "invoiceDate": "2026-01-01",
        "invoiceDateDisplay": "01-Jan-2026",
        "PO": "PO-0001", "DN": "DN-0001", "CNIC": "DC-0001",
        "currency": "PKR", "issueTime": "10:30 AM",
        "saleType": "Goods at standard rate (default)",
        "deliveryDate": "05-Jan-2026",
        # A sample number so the preview shows the QR exactly where a real
        # submitted invoice will carry it.
        "fbrInvoiceNumber": "0000000SAMPLEPREVIEW0000",

        "items": items,
        "totalExcl": round(excl_total, 2), "totalTax": round(tax_total, 2),
        "totalInclusive": round(excl_total + tax_total, 2),
        "amountInWords": "sample amount only",
        "showFurtherTax": False,
        "customFields": [{"label": "Sample Field", "value": "Sample value",
                          "name": "Sample Field"}],
    }


def add_template_routes(app, get_db_connection, generate_qr_base64):
    """Register the template routes.

    `generate_qr_base64(text)` is passed in rather than imported so this module
    stays independent of app.py.
    """

    # parts.html resolves items-table columns through this. Registered as a
    # Jinja global because every invoice render path -- this module, the PDF
    # helpers in app.py, the Excel path -- goes through parts.html, and none
    # of them should have to remember to pass it in.
    app.jinja_env.globals.setdefault("item_columns", invoice_spec.item_columns)
    app.jinja_env.globals.setdefault("invoice_field", resolve_field)
    app.jinja_env.globals.setdefault("meta_rows", meta_rows)

    # Postgres SQLSTATEs for "you are running ahead of your schema".
    UNDEFINED_COLUMN = "42703"
    UNDEFINED_TABLE = "42P01"
    SCHEMA_HINT = ("This screen needs database columns that have not been "
                   "created yet. Run:  python scripts/migrate.py")

    def _connection_identity():
        """Which database the app is actually talking to.

        Worth the extra round trip only on the error path, and it is the one
        fact that separates "the migration has not been run" from "the
        migration was run somewhere else" -- which look identical from the
        browser and waste a great deal of time.
        """
        conn = cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT current_database(), current_user, "
                        "inet_server_addr()::text, inet_server_port()")
            name, user, host, port = cur.fetchone()
            return f"{name} as {user} on {host or 'local'}:{port or '?'}"
        except Exception:
            return None
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    def _schema_payload(exc):
        """The body for a missing-column failure: what, and where."""
        payload = {"error": SCHEMA_HINT}
        detail = str(exc).strip().splitlines()
        if detail:
            payload["detail"] = detail[0]
        where = _connection_identity()
        if where:
            payload["database"] = where
        return payload

    def _schema_error(exc):
        """True when the database is complaining about a missing column/table.

        Identified by SQLSTATE and by exception class, never by message text:
        the text is server-locale dependent, while psycopg2 populates `pgcode`
        only on errors that actually came back from the server -- a wrapped or
        re-raised error can arrive with it empty. Matching the class name as
        well covers that without importing the driver into this module.
        """
        code = getattr(exc, "pgcode", None) or getattr(
            getattr(exc, "orig", None), "pgcode", None)
        if code in (UNDEFINED_COLUMN, UNDEFINED_TABLE):
            return True
        return type(exc).__name__ in ("UndefinedColumn", "UndefinedTable")

    def _json_errors(view):
        """Make an API view answer with JSON no matter how it fails.

        These endpoints are called with fetch() and their replies are parsed as
        JSON. An unhandled exception reached the browser as Flask's HTML error
        page, so what the client actually saw was "Unexpected token '<'" --
        an error message about our error message, with the real cause only in
        the server log. Now the real cause reaches the screen.
        """
        @wraps(view)
        def guarded(*args, **kwargs):
            try:
                return view(*args, **kwargs)
            except letterhead_import.LetterheadImportError as exc:
                return jsonify({"error": str(exc)}), 400
            except Exception as exc:
                app.logger.exception("%s failed", view.__name__)
                if _schema_error(exc):
                    # The single most likely cause the first time this feature
                    # is deployed, and one nobody can act on from the raw
                    # Postgres text.
                    return jsonify(dict(_schema_payload(exc),
                                        where=view.__name__)), 500
                return jsonify({
                    "error": f"{type(exc).__name__}: {exc}",
                    "where": view.__name__,
                }), 500
        return guarded


    # ------------------------------------------------------------------ read
    # Which of SETTING_COLUMNS the database actually has, read from the
    # catalogue once per process.
    #
    # settings_from_row tolerates a row with keys missing, but a SELECT naming
    # a column that does not exist yet fails outright -- and _load_settings
    # then returns {}, which would silently reset every client to the default
    # template, accent and letterhead until the migration ran. Asking first
    # means a deploy that lands ahead of its migration loses only the setting
    # that migration adds.
    _present_columns = []

    def _settings_columns():
        # Cached only once every column is actually there. While any are
        # missing the catalogue is re-read on each call, which is the state a
        # pending migration puts us in -- so applying that migration takes
        # effect on the next request instead of on the next restart. Once the
        # schema is complete the list can never shrink, so it is cached for
        # good and this costs nothing in normal running.
        if len(_present_columns) == len(SETTING_COLUMNS):
            return _present_columns
        conn = cur = None
        found = []
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s",
                ("clients",),
            )
            present = {row[0] for row in cur.fetchall()}
            found = [col for col in SETTING_COLUMNS if col in present]
            missing = [col for col in SETTING_COLUMNS if col not in present]
            if missing:
                app.logger.warning(
                    "clients is missing template columns %s -- run the "
                    "migrations in migrations/", ", ".join(missing))
        except Exception as exc:
            # No catalogue access: assume the schema is current, which is the
            # behaviour this had before the check existed.
            app.logger.warning("could not inspect clients columns: %s", exc)
            found = list(SETTING_COLUMNS)
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()
        _present_columns[:] = found or list(SETTING_COLUMNS)
        return _present_columns

    def _load_settings(client_id):
        columns = _settings_columns()
        conn = cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT %s FROM clients WHERE id = %%s" % ", ".join(columns),
                (client_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return invoice_templates.settings_from_row(dict(zip(columns, row)))
        except Exception as exc:
            app.logger.warning("template settings read failed: %s", exc)
            # Drop the cached column list so the next request re-inspects the
            # catalogue. This is the case where the cache names a column that
            # is not there; _settings_columns covers the opposite case, a
            # cache that is merely incomplete, by not caching it at all.
            _present_columns.clear()
            return {}
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    def _seller_defaults(client_id):
        """The client's own registered seller details, for preview autofill.

        Reads the same `business_profiles` row the create-invoice form offers
        first (its default, else the first by name), so the designer previews
        the seller block the client's invoices will actually print. Returns {}
        when the client has not set one up yet, and the caller falls back to
        placeholders.
        """
        conn = cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT business_name, address, province, ntn_cnic, strn
                FROM business_profiles
                WHERE client_id = %s
                ORDER BY is_default DESC, business_name
                LIMIT 1
                """,
                (client_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            keys = ("business_name", "address", "province", "ntn_cnic", "strn")
            return {k: v for k, v in zip(keys, row) if v}
        except Exception as exc:
            app.logger.warning("seller defaults read failed: %s", exc)
            return {}
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    def _preview_data(client_id, settings):
        """The sample invoice a preview renders, enriched exactly like a real one.

        Running the sample through `apply_compliance` is what keeps the
        designer honest: a client whose profile prints TIME OF ISSUE and a
        relabelled PURCHASE ORDER # sees those rows here, in that order, with
        those labels -- rather than a generic guess that their real invoices
        then contradict. Clients with no profile are untouched, as everywhere
        else, and fall through to the standard row set in parts.meta().
        """
        data = _sample_invoice(_seller_defaults(client_id))
        profile = get_profile(session.get("username"))
        if profile:
            data = apply_compliance(data, settings, profile)
        return data

    def _client_logo(client_id):
        conn = cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT logo_url FROM clients WHERE id = %s", (client_id,))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception:
            return None
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    def _fbr_logo():
        conn = cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT fbr_logo FROM fbr LIMIT 1;")
            row = cur.fetchone()
            return row[0] if row else None
        except Exception:
            return None
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    # ----------------------------------------------------------------- pages
    # The Jinja globals above are registered either way: parts.html needs them
    # on every render path, including a bespoke template that reuses the shared
    # parts. Only the screens are conditional.
    if not EXPOSE_TEMPLATE_UI:
        return

    @app.route("/settings/templates")
    def template_settings_page():
        if "user_id" not in session:
            return redirect(url_for("index"))
        return render_template(
            "template-settings.html",
            layouts=invoice_templates.gallery(),
            accents=invoice_templates.ACCENT_PRESETS,
            fonts=[(k, v[0]) for k, v in invoice_templates.FONTS.items()],
            densities=list(invoice_templates.DENSITIES.keys()),
            settings=_load_settings(session.get("client_id")),
            default_letterhead_mm=invoice_templates.DEFAULT_LETTERHEAD_MM,
            seller_displays=[(key, label) for key, (label, _, _)
                             in invoice_templates.SELLER_DISPLAY.items()],
        )

    @app.route("/settings/template-builder")
    def template_builder_page():
        if "user_id" not in session:
            return redirect(url_for("index"))
        stored = _load_settings(session.get("client_id"))
        spec = stored.get("custom_spec")
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError:
                spec = None
        # The canvas paints the client's own invoice, not placeholders: their
        # registered seller details, and the exact info rows their profile
        # produces. `sample` is the same payload the PDF preview renders, so
        # what they arrange on screen is what comes out of the printer.
        client_id = session.get("client_id")
        sample = _preview_data(client_id, stored)
        return render_template(
            "template-builder.html",
            vocabulary=invoice_spec.describe(),
            spec=invoice_spec.validate(spec) if spec else invoice_spec.default_spec(),
            name=stored.get("custom_name") or "My Custom Template",
            accents=invoice_templates.ACCENT_PRESETS,
            fonts=[(k, v[0]) for k, v in invoice_templates.FONTS.items()],
            densities=list(invoice_templates.DENSITIES.keys()),
            settings=stored,
            sample=sample,
            sample_rows=meta_rows(sample),
            sample_fields={key: resolve_field(sample, key)
                           for key in invoice_spec.FIELD_KEYS},
            logo_url=_client_logo(client_id) or "",
            letterhead_url=stored.get("letterhead_image_url") or "",
            has_seller_profile=bool(_seller_defaults(client_id)),
        )

    # ------------------------------------------------------------------ save
    @app.route("/api/template-settings", methods=["POST"])
    @_json_errors
    def save_template_settings():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "Not signed in"}), 401

        body = request.get_json(silent=True) or {}
        updates = {}

        chosen = body.get("template")
        if chosen in invoice_templates.LAYOUTS_BY_ID or chosen == "custom":
            updates["tpl_template"] = chosen

        # Run the value through resolve_theme so what is stored is what the
        # renderer would accept -- no invalid colour or font can be persisted.
        probe = invoice_templates.resolve_theme({
            "accent_color": body.get("accent_color"),
            "font": body.get("font"),
            "density": body.get("density"),
            "logo_mm": body.get("logo_mm"),
            "qr_mm": body.get("qr_mm"),
            "letterhead_enabled": bool(body.get("letterhead_enabled")),
            "letterhead_mm": body.get("letterhead_mm"),
        })
        if "accent_color" in body:
            updates["tpl_accent_color"] = probe["accent"]
        if "font" in body:
            updates["tpl_font"] = probe["font_id"]
        if "density" in body:
            updates["tpl_density"] = probe["density"]
        if "logo_mm" in body:
            updates["tpl_logo_mm"] = probe["logo_mm"]
        if "qr_mm" in body:
            updates["tpl_qr_mm"] = probe["qr_mm"]
        if "letterhead_enabled" in body:
            updates["tpl_letterhead_enabled"] = bool(body["letterhead_enabled"])
        if "letterhead_mm" in body:
            updates["tpl_letterhead_mm"] = int(
                probe["letterhead_mm"] or invoice_templates.DEFAULT_LETTERHEAD_MM
            )
        # Stored only when the client picked one. Left unset, resolve_theme
        # decides from whether a letterhead image is present, so a client who
        # never opens this control keeps getting the sensible default even
        # after they later add or remove a letterhead.
        if body.get("seller_display") in invoice_templates.SELLER_DISPLAY:
            updates["tpl_seller_display"] = body["seller_display"]
        elif body.get("seller_display") == "auto":
            updates["tpl_seller_display"] = None

        if not updates:
            return jsonify({"error": "Nothing to save"}), 400

        conn = cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            assignments = ", ".join(f"{col} = %s" for col in updates)
            cur.execute(
                f"UPDATE clients SET {assignments} WHERE id = %s",
                list(updates.values()) + [client_id],
            )
            conn.commit()
        except Exception as exc:
            if conn:
                conn.rollback()
            app.logger.exception("template settings save failed")
            if _schema_error(exc):
                return jsonify(_schema_payload(exc)), 500
            return jsonify({"error": f"Could not save: {exc}"}), 500
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        return jsonify({"ok": True, "saved": list(updates.keys())})

    @app.route("/api/custom-template", methods=["POST"])
    @_json_errors
    def save_custom_template():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "Not signed in"}), 401

        # validate() never raises and always returns a spec that carries the
        # FBR compliance block, so what is stored is always renderable.
        body = request.get_json(silent=True) or {}
        spec = invoice_spec.validate(body.get("spec"))

        # A label only; nothing renders from it, so trimming to the column
        # width is the whole of the validation it needs.
        name = str(body.get("name") or "").replace(chr(0), "").strip()[:60]

        conn = cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE clients SET tpl_custom_spec = %s, tpl_custom_name = %s, "
                "tpl_template = %s WHERE id = %s",
                (json.dumps(spec), name or None, "custom", client_id),
            )
            conn.commit()
        except Exception as exc:
            if conn:
                conn.rollback()
            app.logger.exception("custom template save failed")
            return jsonify({"error": f"Could not save: {exc}"}), 500
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        return jsonify({"ok": True, "spec": spec, "name": name})

    def _update_client(updates, client_id):
        """Write a dict of column -> value onto the client's row.

        Column names are never user input: every caller passes literals from
        this module, so formatting them into the statement cannot be injected.
        Values always go through placeholders.
        """
        if not updates:
            return
        conn = cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            assignments = ", ".join(f"{col} = %s" for col in updates)
            cur.execute(
                f"UPDATE clients SET {assignments} WHERE id = %s",
                list(updates.values()) + [client_id],
            )
            conn.commit()
        except Exception:
            if conn:
                conn.rollback()
            raise
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    def _uploads_dir(subdir):
        directory = os.path.join("static", "uploads", subdir)
        os.makedirs(directory, exist_ok=True)
        return directory

    def _handle_image_upload(subdir, column, extra=None):
        """Shared POST/DELETE handling for the letterhead and the logo.

        Both store a URL on `clients` and both write into static/uploads, so
        the validation that matters -- extension allow-list, size ceiling, and
        never trusting the uploaded filename on disk -- lives in one place
        rather than being copied and drifting.

        `extra` is applied on both the upload and the removal, so a column that
        qualifies the image cannot survive the image it describes.
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "Not signed in"}), 401

        if request.method == "DELETE":
            _update_client(dict(extra or {}, **{column: None}), client_id)
            return jsonify({"ok": True, "url": None})

        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify({"error": "No file received"}), 400

        ext = os.path.splitext(upload.filename)[1].lower()
        if ext not in ALLOWED_IMAGE_EXT:
            return jsonify({"error": "Use a PNG, JPG or WebP image"}), 400

        blob = upload.read()
        if len(blob) > MAX_LETTERHEAD_BYTES:
            return jsonify({"error": "Image must be 2MB or smaller"}), 400
        if not blob:
            return jsonify({"error": "File is empty"}), 400

        directory = _uploads_dir(subdir)
        # Never trust the uploaded filename on disk.
        name = f"{client_id}-{uuid.uuid4().hex}{ext}"
        with open(os.path.join(directory, name), "wb") as handle:
            handle.write(blob)

        url = f"/static/uploads/{subdir}/{name}"
        _update_client(dict(extra or {}, **{column: url}), client_id)
        return jsonify({"ok": True, "url": url})

    @app.route("/api/letterhead", methods=["POST", "DELETE"])
    @_json_errors
    def letterhead_upload():
        """Upload or clear a letterhead graphic the client prepared themselves.

        Full-bleed is cleared here on purpose. It means "this image is a crop
        of the client's own page, print it edge to edge at 1:1", which is only
        true of something the importer produced. A hand-uploaded graphic that
        inherited the flag from an earlier import would be stretched across the
        sheet.
        """
        return _handle_image_upload("letterheads", "tpl_letterhead_url",
                                    extra={"tpl_letterhead_fullbleed": False})

    # ------------------------------------------------- letterhead importer
    # Two steps on purpose. Rendering a PDF page is the slow part and the part
    # that can fail on a damaged file, so it happens once, up front, and the
    # client then adjusts the cut line against the picture they can see rather
    # than against a number they have to guess.
    PAGE_H_KEY = "tlp:page_h_mm"

    def _sweep_import_sources(directory, client_id):
        """Drop this client's earlier rendered pages.

        A source page only exists between the two steps. Abandoning the import
        -- closing the tab at the crop step -- would otherwise leave a
        full-page PNG behind for good, so each new import clears the last one.
        """
        prefix = letterhead_import.source_prefix(client_id)
        try:
            names = os.listdir(directory)
        except OSError:
            return
        for name in names:
            if name.startswith(prefix) and name.endswith(".png"):
                try:
                    os.remove(os.path.join(directory, name))
                except OSError:
                    pass

    @app.route("/api/letterhead/import", methods=["POST"])
    @_json_errors
    def letterhead_import_render():
        """Step 1: render page one of an existing invoice and propose a cut."""
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "Not signed in"}), 401

        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify({"error": "No file received"}), 400

        try:
            image, page_w_mm, page_h_mm = letterhead_import.render_first_page(
                upload.read(), upload.filename)
        except letterhead_import.LetterheadImportError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            app.logger.exception("letterhead import: render failed")
            return jsonify({"error": "That file could not be read"}), 400

        low, high = letterhead_import.bounds_mm(page_h_mm)
        detected = letterhead_import.detect_masthead_mm(image, page_h_mm)
        # A page with no ink in its top half gives us nothing to detect. Say so
        # rather than presenting the default as if it had been measured.
        suggested = detected if detected is not None else min(
            high, float(invoice_templates.DEFAULT_LETTERHEAD_MM))

        directory = _uploads_dir("letterheads")
        _sweep_import_sources(directory, client_id)
        name = letterhead_import.source_name(client_id, uuid.uuid4().hex)

        # The page height travels inside the PNG rather than through the
        # browser: step 2 needs it to turn millimetres into pixels, and a value
        # the client could edit would decouple the crop from the reservation.
        from PIL.PngImagePlugin import PngInfo
        meta = PngInfo()
        meta.add_text(PAGE_H_KEY, repr(float(page_h_mm)))
        try:
            image.save(os.path.join(directory, name), format="PNG", pnginfo=meta)
        except Exception:
            app.logger.exception("letterhead import: could not save source page")
            return jsonify({"error": "Could not store the rendered page"}), 500

        return jsonify({
            "ok": True,
            "source": name,
            "source_url": f"/static/uploads/letterheads/{name}",
            "page_w_mm": round(page_w_mm, 1),
            "page_h_mm": round(page_h_mm, 1),
            "suggested_mm": suggested,
            "min_mm": low,
            "max_mm": high,
            "detected": detected is not None,
        })

    @app.route("/api/letterhead/import/commit", methods=["POST"])
    @_json_errors
    def letterhead_import_commit():
        """Step 2: crop the confirmed band and make it the letterhead."""
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "Not signed in"}), 401

        body = request.get_json(silent=True) or {}
        source = body.get("source")
        # `source` comes from the browser. source_is_mine matches the whole
        # basename against a pattern carrying this client id, which rules out
        # both traversal and reaching another client's rendered page.
        if not letterhead_import.source_is_mine(source, client_id):
            return jsonify({"error": "That import is no longer available. "
                                     "Upload the invoice again"}), 400

        path = os.path.join(_uploads_dir("letterheads"), os.path.basename(source))
        if not os.path.exists(path):
            return jsonify({"error": "That import has expired. "
                                     "Upload the invoice again"}), 400

        try:
            from PIL import Image
            with Image.open(path) as handle:
                handle.load()
                page_h_mm = float((handle.text or {}).get(PAGE_H_KEY) or 0)
                image = handle.convert("RGB")
        except Exception:
            app.logger.exception("letterhead import: could not reopen source page")
            return jsonify({"error": "That import could not be read. "
                                     "Upload the invoice again"}), 400

        if page_h_mm <= 0:                     # a source written before PAGE_H_KEY
            page_h_mm = letterhead_import.A4_W_MM * image.height / image.width

        crop, mm = letterhead_import.crop_masthead(image, page_h_mm, body.get("mm"))

        name = f"{client_id}-{uuid.uuid4().hex}.png"
        try:
            crop.save(os.path.join(_uploads_dir("letterheads"), name), format="PNG")
        except Exception:
            app.logger.exception("letterhead import: could not save crop")
            return jsonify({"error": "Could not store the letterhead"}), 500

        url = f"/static/uploads/letterheads/{name}"
        try:
            # Reservation and crop are written together and are the same
            # number: the band is printed back at exactly the size it was cut.
            # Enabling the letterhead here is the point of the whole flow --
            # an import that left it switched off would print nothing.
            _update_client({
                "tpl_letterhead_url": url,
                "tpl_letterhead_fullbleed": True,
                "tpl_letterhead_enabled": True,
                "tpl_letterhead_mm": int(mm),
            }, client_id)
        except Exception as exc:
            app.logger.exception("letterhead import: save failed")
            if _schema_error(exc):
                return jsonify(_schema_payload(exc)), 500
            return jsonify({"error": f"Could not save: {exc}"}), 500

        try:
            os.remove(path)
        except OSError:
            pass

        return jsonify({"ok": True, "url": url, "mm": int(mm)})

    @app.route("/api/client-logo", methods=["POST", "DELETE"])
    @_json_errors
    def client_logo_upload():
        """Upload or clear the logo the Company Logo element prints.

        Stored on `clients.logo_url`, which is the column every invoice render
        path already reads, so a logo uploaded in the designer shows up on
        invoices printed from the built-in layouts too -- rather than being a
        second, designer-only logo that then disagrees with the rest of the
        software.
        """
        return _handle_image_upload("logos", "logo_url")

    # --------------------------------------------------------------- preview
    # A preview has two audiences. While the client is choosing, they want to
    # see the change *now* -- so the same Jinja templates are served straight
    # to an iframe, where _theme.html's `@media screen` block rebuilds the
    # sheet a browser would otherwise ignore. Rasterising a PDF for every
    # slider nudge cost seconds each time and gave a page they could not
    # scroll smoothly or select text in.
    #
    # The PDF path stays, because it is the only thing that proves what the
    # printer will do: pagination, page breaks and the letterhead reservation
    # on page 1 only. The screen offers it as an explicit check.
    def wants_html():
        return (request.args.get("format") or "").lower() == "html"

    def _preview_html(markup):
        return markup, 200, {
            "Content-Type": "text/html; charset=utf-8",
            # Previews are regenerated constantly and must never be served
            # from cache, or a client changes a setting and sees the old sheet.
            "Cache-Control": "no-store",
        }

    @app.route("/api/template-preview")
    def template_preview():
        """Render a sample invoice as a PDF, using the app's real PDF path.

        Query params override the stored settings so the gallery can preview a
        template the client has not committed to yet.
        """
        if "user_id" not in session:
            return jsonify({"error": "Not signed in"}), 401

        client_id = session.get("client_id")
        settings = _load_settings(client_id)

        for key in ("template", "accent_color", "font", "density",
                    "seller_display"):
            if request.args.get(key):
                settings[key] = request.args[key]
        if request.args.get("letterhead_enabled") is not None:
            settings["letterhead_enabled"] = request.args.get("letterhead_enabled") == "1"
        if request.args.get("letterhead_mm"):
            settings["letterhead_mm"] = request.args["letterhead_mm"]

        spec = settings.get("custom_spec")
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError:
                spec = None
        if settings.get("template") == "custom":
            spec = invoice_spec.validate(spec)
            settings["custom_spec"] = spec

        data = _preview_data(client_id, settings)
        html = render_template(
            invoice_templates.template_path(settings),
            data=data,
            theme=invoice_templates.resolve_theme(settings),
            settings=settings,
            custom_spec=spec,
            qr_base64=generate_qr_base64(data["fbrInvoiceNumber"]),
            client_logo_url=_client_logo(client_id),
            fbr_logo_url=_fbr_logo(),
            username=session.get("username"),
        )

        if wants_html():
            return _preview_html(html)

        from weasyprint import HTML as WeasyHTML

        stream = io.BytesIO()
        WeasyHTML(string=html, base_url=request.url_root).write_pdf(stream)
        stream.seek(0)
        return send_file(stream, mimetype="application/pdf",
                         download_name="template-preview.pdf")

    @app.route("/api/custom-template/preview", methods=["POST"])
    def custom_template_preview():
        """Preview an unsaved builder spec."""
        if "user_id" not in session:
            return jsonify({"error": "Not signed in"}), 401

        client_id = session.get("client_id")
        body = request.get_json(silent=True) or {}
        settings = _load_settings(client_id)
        settings["template"] = "custom"
        spec = invoice_spec.validate(body.get("spec"))
        settings["custom_spec"] = spec
        for key in ("accent_color", "font", "density"):
            if body.get(key):
                settings[key] = body[key]

        data = _preview_data(client_id, settings)
        html = render_template(
            "invoices/custom.html",
            data=data,
            theme=invoice_templates.resolve_theme(settings),
            settings=settings,
            custom_spec=spec,
            qr_base64=generate_qr_base64(data["fbrInvoiceNumber"]),
            client_logo_url=_client_logo(client_id),
            fbr_logo_url=_fbr_logo(),
            username=session.get("username"),
        )

        if wants_html():
            return _preview_html(html)

        from weasyprint import HTML as WeasyHTML

        stream = io.BytesIO()
        WeasyHTML(string=html, base_url=request.url_root).write_pdf(stream)
        stream.seek(0)
        return send_file(stream, mimetype="application/pdf",
                         download_name="template-preview.pdf")
