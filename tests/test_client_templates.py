"""
Tests for the hand-built per-client invoice replicas.

Each of these is a copy of one client's own invoice, so they are all different
by design and there is nothing to assert about how they *look*. What must hold
for every one of them, no matter whose sheet it copies, is that it still prints
a valid sales tax invoice:

  * the three FBR marks -- invoice number, Digital Invoicing logo, QR code
  * the identifiers FBR requires on the face of the document -- seller and
    buyer NTN and STRN, HS code, quantity, rate, value excluding tax, sales
    tax, and the totals

A replica that drops one of those looks right and is worthless. Adding a new
client's template to CLIENT_TEMPLATES below is what puts it under this guard.

Run:  python3 -m unittest discover -s tests -v
"""

import base64
import datetime
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flask import Flask, render_template  # noqa: E402

import invoice_templates  # noqa: E402

# template file -> the client it was built for. Add a row when you build one.
CLIENT_TEMPLATES = {
    "invoice_apple_international.html": "3520224169621",
    "invoice_ak_international.html": "3520212803454",
    "invoice_paper_land.html": "4242880",
    "invoice_hannan_traders.html": "3520230962516",
    "invoice_paper_experts.html": "3520261094743",
    "invoice_fk_printers.html": "3520235613477",
}

FBR_NUMBER = "3520224169621DIVROFIR912774"
FBR_LOGO = "/static/uploads/fbr-di-logo.png"
QR_B64 = base64.b64encode(b"fake-qr-png-bytes").decode()

PAYLOAD = {
    "sellerBusinessName": "Apple International",
    "sellerLegalName": "Apple International",
    "sellerAddress": "13/2-A, Chatterjee Road, Urdu Bazar, Lahore",
    "sellerNTNCNIC": "1203281-6",
    "sellerSTRN": "3520224169621",
    "sellerProvince": "Punjab",
    "buyerBusinessName": "KHAN SONS TRADING CORPORATION",
    "buyerAddress": "5-A FAISAL TOWN, LAHORE.",
    "buyerNTNCNIC": "0178847-7",
    "buyerSTRN": "3520161006727",
    "buyerRegistrationType": "Registered",
    "buyerProvince": "Punjab",
    "invoiceType": "Sale Invoice",
    "invoiceRefNo": "001",
    "invoiceDate": "2026-09-01",
    "fbrInvoiceNumber": FBR_NUMBER,
    "items": [{
        "productDescription": "Thermal Paper",
        "hsCode": "4811.5910",
        "uoM": "Kg",
        "quantity": "191.00",
        "unitrate": 669,
        "rate": "18%",
        "valueSalesExcludingST": 127779,
        "salesTaxApplicable": 23000,
        "totalValues": 150779,
    }],
    "totalExcl": 127779,
    "totalTax": 23000,
    "totalInclusive": 150779,
    "amountInWords": "one hundred and fifty thousand seven hundred and seventy-nine rupees only",
}


def build_app():
    app = Flask(__name__, root_path=ROOT, template_folder="templates",
                static_folder="static")

    # The two filters app.py registers; the templates use both.
    @app.template_filter("datetimeformat")
    def datetimeformat(value):
        try:
            return datetime.datetime.strptime(value, "%Y-%m-%d").strftime("%d %B %Y")
        except Exception:
            return value

    @app.template_filter("comma_format")
    def comma_format(value):
        try:
            return "{:,.2f}".format(float(value))
        except (ValueError, TypeError):
            return value

    return app


def render(template, data=None, qr=QR_B64, logo=FBR_LOGO, client_logo=None):
    app = build_app()
    with app.test_request_context("/"):
        return render_template(
            template,
            data=data or PAYLOAD,
            qr_base64=qr,
            client_logo_url=client_logo,
            fbr_logo_url=logo,
            username=CLIENT_TEMPLATES[template],
            settings={},
            theme={},
        )


class ComplianceMarks(unittest.TestCase):
    """The three things every FBR invoice must carry."""

    def test_every_template_prints_the_fbr_invoice_number(self):
        for template in CLIENT_TEMPLATES:
            self.assertIn(FBR_NUMBER, render(template), template)

    def test_every_template_prints_the_qr_code(self):
        for template in CLIENT_TEMPLATES:
            self.assertIn(QR_B64, render(template), template)

    def test_every_template_prints_the_digital_invoicing_logo(self):
        for template in CLIENT_TEMPLATES:
            self.assertIn(FBR_LOGO, render(template), template)

    def test_the_marks_are_absent_only_before_submission(self):
        # An invoice with no FBR number has not been submitted, so there is
        # nothing for the QR to encode. It must say so rather than printing a
        # QR that resolves to nothing.
        unsubmitted = dict(PAYLOAD, fbrInvoiceNumber="")
        for template in CLIENT_TEMPLATES:
            markup = render(template, unsubmitted, qr="")
            self.assertNotIn(FBR_NUMBER, markup, template)
            self.assertIn("Not yet submitted", markup, template)


class RequiredIdentifiers(unittest.TestCase):
    """What a sales tax invoice has to show on its face."""

    CASES = {
        "seller NTN": "1203281-6",
        "seller STRN": "3520224169621",
        "buyer NTN": "0178847-7",
        "buyer STRN": "3520161006727",
        "HS code": "4811.5910",
        "quantity": "191.00",
        "unit price": "669.00",
        "value excluding tax": "127,779.00",
        "sales tax": "23,000.00",
        "value including tax": "150,779.00",
    }

    # F.K. Printers asked for the HS code to be left off the printed sheet --
    # as a column first, then from under the description. Their own invoice
    # never carried one. It is still submitted to FBR on every line, so this
    # waives how the document *looks*, not what is filed. Nothing else is
    # waived for them, and no other client waives anything: a new entry here
    # needs the client to have asked for it.
    WAIVED = {
        "invoice_fk_printers.html": {"HS code"},
    }

    def test_every_template_prints_every_required_field(self):
        for template in CLIENT_TEMPLATES:
            markup = render(template)
            waived = self.WAIVED.get(template, set())
            for label, value in self.CASES.items():
                if label in waived:
                    continue
                self.assertIn(value, markup, f"{template}: {label} missing")

    def test_nothing_is_waived_that_the_client_did_not_ask_to_waive(self):
        # A waiver is a decision, not a way around a failing assertion. This
        # fails if one is left behind for a template that no longer exists or
        # names a field that is not checked.
        for template, labels in self.WAIVED.items():
            self.assertIn(template, CLIENT_TEMPLATES, template)
            for label in labels:
                self.assertIn(label, self.CASES, label)

    def test_buyer_and_seller_details_survive_a_missing_logo(self):
        # Most clients have no logo file; the masthead must still identify them.
        for template in CLIENT_TEMPLATES:
            markup = render(template, client_logo=None)
            self.assertIn("Apple International", markup, template)


class BrandArtwork(unittest.TestCase):
    """A client's identity must not depend on a URL resolving.

    This is the bug these tests exist for. `client_logo_url` is a database
    column, and app.py renders invoice PDFs on three routes that do not all
    turn it into an absolute URL -- so the logo appeared on one route and was
    replaced by a typed fallback on another. Artwork inlined in the template
    prints the same everywhere.
    """

    # Templates whose brand partial supplies the masthead.
    WITH_PARTIALS = {
        "invoice_apple_international.html": "brand/3520224169621.html",
        "invoice_ak_international.html": "brand/3520212803454.html",
        "invoice_paper_land.html": "brand/4242880.html",
        "invoice_hannan_traders.html": "brand/3520230962516.html",
        "invoice_paper_experts.html": "brand/3520261094743.html",
        "invoice_fk_printers.html": "brand/3520235613477.html",
    }

    def test_artwork_renders_with_no_logo_url_at_all(self):
        for template in self.WITH_PARTIALS:
            markup = render(template, client_logo=None)
            self.assertIn("data:image/png;base64,", markup,
                          f"{template}: no inlined artwork")

    def test_artwork_is_identical_with_and_without_a_logo_url(self):
        # A stale or wrong clients.logo_url must not change the output.
        for template in self.WITH_PARTIALS:
            without = render(template, client_logo=None)
            with_url = render(template, client_logo="/static/uploads/logos/whatever.png")
            self.assertEqual(without, with_url, template)

    def test_the_brand_partial_exists_and_is_inlined(self):
        for template, partial in self.WITH_PARTIALS.items():
            path = os.path.join(ROOT, "templates", *partial.split("/"))
            self.assertTrue(os.path.exists(path), partial)
            body = io.open(path, encoding="utf-8").read()
            self.assertIn("data:image/png;base64,", body, partial)
            # A linked src is exactly what this partial exists to avoid.
            self.assertNotIn('src="/static/', body, partial)


class Wiring(unittest.TestCase):
    """A template nobody can be assigned to is dead code."""

    def test_every_client_template_is_assignable(self):
        # resolve_template_path only returns a filename it recognises, so a
        # template missing from LEGACY_TEMPLATES can never be reached by
        # setting clients.tpl_template.
        for template in CLIENT_TEMPLATES:
            self.assertIn(template, invoice_templates.LEGACY_TEMPLATES, template)
            self.assertEqual(
                template,
                invoice_templates.resolve_template_path(
                    {"template": template}, "invoice_template3.html"),
                template)

    def test_a_client_without_a_choice_is_unaffected(self):
        # The whole point of assigning by row: everyone else keeps the template
        # the username chain in app.py already gave them.
        self.assertEqual(
            "invoice_template3.html",
            invoice_templates.resolve_template_path({}, "invoice_template3.html"))

    def test_every_client_template_file_exists(self):
        for template in CLIENT_TEMPLATES:
            self.assertTrue(
                os.path.exists(os.path.join(ROOT, "templates", template)),
                template)


if __name__ == "__main__":
    unittest.main()
