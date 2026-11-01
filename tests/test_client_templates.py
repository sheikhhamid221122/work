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
import re
import sys
import unittest
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flask import Flask, render_template  # noqa: E402

import advance_tax  # noqa: E402
import client_features  # noqa: E402
import invoice_templates  # noqa: E402

# Only ZahidSteelPagination needs these: it is the one test here that has to
# look at real pages rather than at markup. app.py depends on WeasyPrint, so
# in practice they are always present; the guard just keeps the rest of this
# module runnable if the PDF stack is ever stripped out of a CI image.
try:
    import pypdfium2
    from PIL import Image
    from weasyprint import HTML as WeasyHTML
except Exception:  # pragma: no cover
    pypdfium2 = Image = WeasyHTML = None

# template file -> the client it was built for. Add a row when you build one.
# The value is what `username` is rendered with, so it is the client's
# username -- except for Global Surface, who are keyed on their users.id
# everywhere (see client_features.py) and whose template does not read the
# username at all.
CLIENT_TEMPLATES = {
    "invoice_apple_international.html": "3520224169621",
    "invoice_ak_international.html": "3520212803454",
    "invoice_paper_land.html": "4242880",
    "invoice_hannan_traders.html": "3520230962516",
    "invoice_paper_experts.html": "3520261094743",
    "invoice_fk_printers.html": "3520235613477",
    "invoice_saad_enterprises.html": "3520287506167",
    "invoice_power_vision.html": "3740545496385",
    "invoice_bilal_traders.html": "3740549685449",
    "invoice_bilal_brothers.html": "3740582041351",
    "invoice_aero_alliance.html": "3740564149343",
    "invoice_beacon_alliance.html": "3740545582835",
    "invoice_salon_marketings.html": "3520223163127",
    "invoice_zahid_steel.html": "J527785",
    "invoice_global_surface.html": "ed156fdd-086e-480c-be18-a2c85d9e31b1",
}

# The paper clients who asked for a "No. of Reams" column beside the quantity.
# It is a figure they want printed and nothing more: no total is derived from
# it, and app.py drops it before the payload reaches FBR, whose item schema has
# no such field. Nobody else's replica carries the column.
REAMS_TEMPLATES = {
    "invoice_apple_international.html",
    "invoice_ak_international.html",
    "invoice_paper_land.html",
    "invoice_hannan_traders.html",
    "invoice_paper_experts.html",
}

# Distinctive enough that it cannot collide with a figure already in PAYLOAD.
REAMS = "9876"

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


class _ItemsTable(HTMLParser):
    """Cell count per row of the first <table class="items">, colspans summed.

    Used to prove the replicas' items tables stay rectangular. A row one cell
    short of its header does not fail to render -- it silently shifts every
    figure after it into the wrong column, which is the one way a replica can
    be wrong and still look like an invoice.
    """

    def __init__(self):
        HTMLParser.__init__(self)
        self.rows = []
        self._depth = 0
        self._cells = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            if self._depth or "items" in (attrs.get("class") or "").split():
                self._depth += 1
        elif self._depth == 1:
            if tag == "tr":
                self._cells = 0
            elif tag in ("td", "th") and self._cells is not None:
                try:
                    self._cells += int(attrs.get("colspan", 1))
                except (TypeError, ValueError):
                    self._cells += 1

    def handle_endtag(self, tag):
        if tag == "table" and self._depth:
            self._depth -= 1
        elif tag == "tr" and self._depth == 1 and self._cells is not None:
            self.rows.append(self._cells)
            self._cells = None


def items_table_rows(markup):
    parser = _ItemsTable()
    parser.feed(markup)
    return parser.rows


def with_reams(count=REAMS):
    payload = dict(PAYLOAD)
    payload["items"] = [dict(PAYLOAD["items"][0], numberOfReams=count)]
    return payload


class ItemsTableAlignment(unittest.TestCase):
    """Every row of an items table must be as wide as its header."""

    def test_every_row_has_as_many_cells_as_the_header(self):
        # Filler rows and the in-table totals row are written cell by cell, so
        # adding a column means touching three places in the same template.
        # Miss one and the totals print under the wrong heading.
        for template in CLIENT_TEMPLATES:
            for payload in (PAYLOAD, with_reams()):
                rows = items_table_rows(render(template, payload))
                self.assertTrue(rows, f"{template}: no items table found")
                self.assertEqual(
                    {rows[0]}, set(rows),
                    f"{template}: rows are {rows}, header has {rows[0]} cells")


class ReamsColumn(unittest.TestCase):
    """The paper clients' printed-only reams count."""

    def test_the_five_clients_print_the_column_and_the_figure(self):
        for template in REAMS_TEMPLATES:
            markup = render(template, with_reams())
            self.assertIn("Reams", markup, template)
            self.assertIn(f">{REAMS}<", markup, f"{template}: figure missing")

    def test_no_other_replica_grows_the_column(self):
        # A client who did not ask for it must not get it, whatever the payload
        # happens to carry.
        for template in set(CLIENT_TEMPLATES) - REAMS_TEMPLATES:
            self.assertNotIn("Reams", render(template, with_reams()), template)

    def test_nothing_on_the_invoice_is_calculated_from_it(self):
        # The sheet with a reams figure and the sheet without must be the same
        # document apart from that one cell -- which also shows the cell is
        # left empty, not printed as 0, when the line has no figure.
        for template in REAMS_TEMPLATES:
            with_figure = render(template, with_reams())
            without = render(template)
            self.assertEqual(
                without, with_figure.replace(f">{REAMS}<", "><"), template)


class AdvanceTax(unittest.TestCase):
    """Salon Marketings' printed-only Advance Tax u/s 236(h).

    It is income tax the seller collects from a registered buyer, shown on
    the buyer's copy of the invoice and never filed -- so the test that
    matters most here is the one proving the FILED figures are untouched.
    """

    TEMPLATE = "invoice_salon_marketings.html"

    def payload(self, **overrides):
        data = dict(PAYLOAD)
        data.update(overrides)
        return advance_tax.apply(data, CLIENT_TEMPLATES[self.TEMPLATE])

    def test_the_default_rate_is_half_a_percent_of_the_value_including_tax(self):
        data = self.payload()
        # 150,779.00 * 0.5% = 753.895 -> 753.90, half-up.
        self.assertEqual(753.90, data["advanceTaxAmount"])
        self.assertEqual(151532.90, data["totalWithAdvanceTax"])

    def test_the_rate_is_the_users_to_set(self):
        data = self.payload(advanceTaxPercent="1.5")
        self.assertEqual(2261.69, data["advanceTaxAmount"])
        self.assertIn("1.5%", render(self.TEMPLATE, data))

    def test_a_zero_rate_leaves_the_line_off(self):
        data = self.payload(advanceTaxPercent=0)
        self.assertNotIn("showAdvanceTax", data)
        self.assertNotIn(advance_tax.LABEL, render(self.TEMPLATE, data))

    def test_an_unregistered_buyer_gets_no_advance_tax(self):
        data = self.payload(buyerRegistrationType="Unregistered")
        self.assertNotIn("showAdvanceTax", data)
        self.assertNotIn(advance_tax.LABEL, render(self.TEMPLATE, data))

    def test_nothing_that_is_filed_changes(self):
        # The whole point: FBR holds a sales tax invoice that ends at the
        # Total row. If any of these moved, the printed sheet and the filed
        # record would disagree.
        data = self.payload()
        for key in ("totalExcl", "totalTax", "totalInclusive"):
            self.assertEqual(PAYLOAD[key], data[key], key)
        self.assertEqual(PAYLOAD["items"], data["items"])

    def test_the_amount_in_words_is_the_figure_the_buyer_pays(self):
        data = self.payload()
        self.assertIn("one hundred and fifty-one thousand", data["amountInWords"])
        self.assertIn("Fifty-One Thousand", render(self.TEMPLATE, data))

    def test_the_line_and_the_new_total_both_print(self):
        markup = render(self.TEMPLATE, self.payload())
        self.assertIn(advance_tax.LABEL, markup)
        self.assertIn("753.90", markup)
        self.assertIn("Total Value Including Taxes", markup)
        self.assertIn("151,532.90", markup)

    def test_no_other_replica_grows_the_line(self):
        # A client who did not ask for it must not get it, whatever the
        # payload happens to carry.
        data = dict(PAYLOAD, showAdvanceTax=True, advanceTaxLabel=advance_tax.LABEL,
                    advanceTaxRateLabel="0.5%", advanceTaxAmount=753.90,
                    totalWithAdvanceTax=151532.90)
        for template in set(CLIENT_TEMPLATES) - {self.TEMPLATE}:
            self.assertNotIn(advance_tax.LABEL, render(template, data), template)

    def test_only_the_listed_clients_have_the_line_applied_at_all(self):
        for template, username in CLIENT_TEMPLATES.items():
            data = advance_tax.apply(dict(PAYLOAD), username)
            self.assertEqual(
                template == self.TEMPLATE,
                bool(data.get("showAdvanceTax")),
                template)

    def test_the_derived_figures_never_reach_the_stored_payload(self):
        # Only the rate is stored; everything else is recomputed at render
        # time, so re-printing an old invoice reproduces the same sheet.
        data = self.payload()
        advance_tax.strip_derived(data)
        for key in advance_tax.DERIVED_KEYS:
            self.assertNotIn(key, data, key)
        self.assertIn("advanceTaxPercent", data)

    def test_the_rate_the_form_offers_is_the_rate_the_server_uses(self):
        # The one copy of the client list that cannot import advance_tax.py
        # is the set in the create-invoice form. It drifting is how the form
        # ends up offering a field the sheet does not print.
        form = io.open(os.path.join(ROOT, "templates", "create-invoice.html"),
                       encoding="utf-8").read()
        block = form.split("const ADVANCE_TAX_CLIENTS = new Set([")[1].split("]);")[0]
        self.assertEqual(
            advance_tax.ADVANCE_TAX_USERNAMES,
            set(re.findall(r"'([^']+)'", block)))
        self.assertIn(
            "const ADVANCE_TAX_DEFAULT_PERCENT = %s;" % advance_tax.DEFAULT_PERCENT,
            form)


class ZahidSteelCheckDigit(unittest.TestCase):
    """Zahid Steel's NTN always prints with its check digit.

    Their seller profile may still hold the bare seven characters, and the
    client asked for J527785-7 on the face of every sheet. The check digit is
    derived from those seven, so the printed sheet and the filed record do not
    actually disagree -- but a template that rewrites an identifier has to be
    held to a narrow scope, which is what the third test is for.
    """

    TEMPLATE = "invoice_zahid_steel.html"
    CANONICAL = "J527785-7"

    def test_the_bare_ntn_prints_with_its_check_digit(self):
        markup = render(self.TEMPLATE, dict(PAYLOAD, sellerNTNCNIC="J527785"))
        self.assertIn(self.CANONICAL, markup)

    def test_an_ntn_that_already_carries_it_is_left_alone(self):
        markup = render(self.TEMPLATE, dict(PAYLOAD, sellerNTNCNIC=self.CANONICAL))
        self.assertIn(self.CANONICAL, markup)
        self.assertNotIn("J527785-7-7", markup)

    def test_no_other_sellers_ntn_is_ever_rewritten(self):
        # The whole risk of the substitution: it must key on this one
        # company's number and silently pass everything else through.
        markup = render(self.TEMPLATE, dict(PAYLOAD, sellerNTNCNIC="1203281-6"))
        self.assertIn("1203281-6", markup)
        self.assertNotIn(self.CANONICAL, markup)


@unittest.skipIf(WeasyHTML is None, "WeasyPrint/pypdfium2 not installed")
class ZahidSteelPagination(unittest.TestCase):
    """Five line items must fit one page, and a sixth must not break the sheet.

    Zahid Steel's items box is five filler rows deep, so five is the sheet's
    designed capacity. Pagination here is fragile in one specific way: the
    letterhead header, the body type scale and the closing block all compete
    for the same vertical space, so enlarging the logo or the text silently
    costs a row. That happened twice while this template was being built --
    once leaving the closing block alone on a page of its own with half of
    page 1 blank -- which is why it is pinned here rather than eyeballed.

    This is the only test in this module that renders real pages. Markup
    cannot answer "does it fit".
    """

    TEMPLATE = "invoice_zahid_steel.html"
    CAPACITY = 5

    # Long enough to wrap in the description column. A one-word description
    # fits far more rows per page and would make this test pass while the
    # real thing overflowed, which is exactly the hole a sample invoice left.
    DESCRIPTION = "DEFORMED STEEL BARS GRADE 60 (16 mm) CONSIGNMENT LINE %d"

    NOTE = "This is an FBR-generated copy"
    TOTAL = "Value Inclusive of Sales Tax"
    STATIONERY = "zahidsteel.com"
    ITEMS_HEADING = "Product Detail"

    @classmethod
    def setUpClass(cls):
        # The real mark, downscaled: the template sizes it `width: 24mm;
        # height: auto`, so only its aspect ratio affects the layout, and a
        # broken image would collapse the row and silently change the fit.
        logo = Image.open(os.path.join(ROOT, "static", "uploads", "FBR LOGO.png"))
        logo.thumbnail((240, 240))
        buffer = io.BytesIO()
        logo.save(buffer, format="PNG")
        cls.fbr_logo = ("data:image/png;base64,"
                        + base64.b64encode(buffer.getvalue()).decode())

    @staticmethod
    def flat(text):
        """Text with every space and line break removed.

        A table cell that wraps comes back from the PDF as several lines, so
        a wrapped description never matches the string that produced it.
        Comparing without whitespace is what stops these assertions passing
        vacuously -- which one of them did before this was added.
        """
        return re.sub(r"\s+", "", text)

    def pages(self, count):
        """Text of each page of a `count`-line invoice, as rendered."""
        data = dict(PAYLOAD, items=[
            dict(PAYLOAD["items"][0], productDescription=self.DESCRIPTION % (i + 1))
            for i in range(count)
        ])
        markup = render(self.TEMPLATE, data, logo=self.fbr_logo)
        pdf = WeasyHTML(string=markup, base_url=ROOT).write_pdf()
        document = pypdfium2.PdfDocument(io.BytesIO(pdf))
        return [document[i].get_textpage().get_text_bounded()
                for i in range(len(document))]

    def test_up_to_five_items_fit_a_single_page(self):
        for count in range(1, self.CAPACITY + 1):
            pages = self.pages(count)
            self.assertEqual(
                1, len(pages),
                "%d line(s) spilled onto %d pages -- the header or the type "
                "scale has grown and eaten a row" % (count, len(pages)))

    def test_a_sixth_line_spills_rather_than_being_dropped(self):
        pages = self.pages(self.CAPACITY + 1)
        self.assertGreater(len(pages), 1)
        self.assertIn(self.flat(self.DESCRIPTION % (self.CAPACITY + 1)),
                      self.flat("".join(pages)),
                      "the overflowing line vanished instead of spilling")

    def test_an_overflowing_invoice_keeps_its_closing_block_together(self):
        # The failure this guards: the totals, the FBR stamp and the closing
        # note split across a page boundary, or the note lands alone on a
        # page, which reads as a second, empty document.
        for count in (self.CAPACITY + 1, self.CAPACITY + 3, self.CAPACITY + 10):
            pages = self.pages(count)
            carrying = [i for i, text in enumerate(pages)
                        if self.flat(self.NOTE) in self.flat(text)]
            self.assertEqual([len(pages) - 1], carrying,
                             "%d lines: the closing note is on pages %s"
                             % (count, carrying))
            self.assertIn(self.flat(self.TOTAL), self.flat(pages[-1]),
                          "%d lines: the totals parted company with the note"
                          % count)

    def test_the_stationery_prints_on_every_page(self):
        # The footer is pre-printed paper: a second page without it is a
        # sheet that does not match the first.
        for count in (1, self.CAPACITY + 1, self.CAPACITY + 10):
            for index, text in enumerate(self.pages(count)):
                self.assertIn(self.flat(self.STATIONERY), self.flat(text),
                              "%d lines: page %d has no footer"
                              % (count, index + 1))

    def test_a_continued_items_table_repeats_its_headings(self):
        # Figures under no headings are unreadable, and worse, look like they
        # belong to whatever column the eye lands on.
        pages = self.pages(self.CAPACITY + 10)
        stem = self.flat(self.DESCRIPTION.split(" LINE")[0])
        carrying = [i for i, text in enumerate(pages) if stem in self.flat(text)]
        self.assertGreater(len(carrying), 1,
                           "expected item rows on more than one page; this "
                           "test is vacuous otherwise")
        for index in carrying:
            self.assertIn(self.flat(self.ITEMS_HEADING), self.flat(pages[index]),
                          "page %d carries item rows with no headings"
                          % (index + 1))


class GlobalSurfaceFields(unittest.TestCase):
    """Global Surface's two additions: four custom fields and a buyer phone.

    Both are printed-only. Neither is derived from, and neither feeds, a
    figure on the sheet -- app.py strips the phone from the FBR payload
    (`PDF_ONLY_INVOICE_FIELDS`) and the custom fields were never in it.
    """

    TEMPLATE = "invoice_global_surface.html"
    CLIENT = CLIENT_TEMPLATES["invoice_global_surface.html"]

    FIELDS = [
        {"name": "PO Number", "value": "4500123456"},
        {"name": "PO Date", "value": "10/03/2026"},
        {"name": "DC Number", "value": "N-453/25-26"},
        {"name": "DC Date", "value": "12/07/2026"},
    ]
    PHONE = "051-5487589-97"

    def test_all_four_custom_fields_print(self):
        # Two is what every other client gets; this sheet carries four, and
        # the third and fourth are the ones a shared cap would have dropped.
        markup = render(self.TEMPLATE, dict(PAYLOAD, customFields=self.FIELDS))
        for field in self.FIELDS:
            self.assertIn(field["name"], markup)
            self.assertIn(field["value"], markup)

    def test_a_fifth_field_is_not_printed(self):
        # Defence in depth. app.py caps the list before it gets here, but a
        # payload stored before the cap existed must not widen the sheet.
        extra = self.FIELDS + [{"name": "Extra Field", "value": "SHOULD-NOT-PRINT"}]
        markup = render(self.TEMPLATE, dict(PAYLOAD, customFields=extra))
        self.assertNotIn("SHOULD-NOT-PRINT", markup)

    def test_an_empty_field_leaves_no_empty_label(self):
        # The name here is deliberately one that appears nowhere else in the
        # template. "PO Date" would pass vacuously: the stylesheet names the
        # four fields in a comment, and a CSS comment IS in the rendered
        # markup.
        fields = [self.FIELDS[0], {"name": "Gate Pass No", "value": ""}]
        markup = render(self.TEMPLATE, dict(PAYLOAD, customFields=fields))
        self.assertIn("PO Number", markup)
        self.assertNotIn("Gate Pass No", markup)

    def test_the_items_box_is_always_five_rows(self):
        # The client asked for a five-row box whatever the invoice holds:
        # one line item and four empty rows, not one row and white space.
        # `items_table_rows` counts the header too.
        for count in (1, 2, 5):
            data = dict(PAYLOAD, items=[PAYLOAD["items"][0]] * count)
            self.assertEqual(6, len(items_table_rows(render(self.TEMPLATE, data))),
                             "%d item(s): the box is not five rows" % count)
        # Past five there is nothing to pad, and no row is dropped either.
        data = dict(PAYLOAD, items=[PAYLOAD["items"][0]] * 7)
        self.assertEqual(8, len(items_table_rows(render(self.TEMPLATE, data))))

    def test_the_hs_code_is_set_like_the_other_figures(self):
        # It was the one value in the row that printed light. `fig` is the
        # class that makes a cell bold Times 10.6pt, like every figure.
        markup = render(self.TEMPLATE, PAYLOAD)
        self.assertIn('<td class="fig">4811.5910</td>', markup)

    def test_the_sheet_carries_no_signature_block(self):
        # The client asked for the Regards / For <company> / signatory block
        # to come off. Their wordmark carries the company name instead.
        markup = render(self.TEMPLATE, PAYLOAD)
        self.assertNotIn("Regards", markup)
        self.assertNotIn("Imran Elahi", markup)

    def test_the_total_payable_row_is_gone_and_the_words_lead_the_summary(self):
        # Their own sheet's Total Payable row was replaced by the summary
        # box, with the amount in words in front of it.
        markup = render(self.TEMPLATE, PAYLOAD)
        self.assertNotIn("Total<br>Payable", markup)
        self.assertIn("Amount in words:", markup)
        self.assertIn("Value Inclusive of Sales Tax", markup)

    def test_the_phone_row_appears_only_when_there_is_a_number(self):
        with_phone = render(self.TEMPLATE, dict(PAYLOAD, buyerPhone=self.PHONE))
        self.assertIn(self.PHONE, with_phone)
        self.assertIn("Phone#", with_phone)
        self.assertNotIn("Phone#", render(self.TEMPLATE, PAYLOAD))

    def test_no_other_replica_grows_a_phone_row(self):
        # A client who did not ask for it must not get it, whatever the
        # payload happens to carry. Salon Marketings asked for the same row
        # and are excluded for that reason, not because they slipped through.
        data = dict(PAYLOAD, buyerPhone=self.PHONE)
        for template in set(CLIENT_TEMPLATES) - {self.TEMPLATE,
                                                 "invoice_salon_marketings.html"}:
            self.assertNotIn(self.PHONE, render(template, data), template)

    def test_the_cap_is_four_for_this_client_and_two_for_everyone_else(self):
        self.assertEqual(4, client_features.custom_fields_max(self.CLIENT))
        self.assertEqual(4, client_features.custom_fields_max(None, self.CLIENT))
        for other in (None, "", "J527785", "3520223163127",
                      "00000000-0000-0000-0000-000000000000"):
            self.assertEqual(2, client_features.custom_fields_max(other), other)

    def test_only_this_client_is_offered_the_phone_field(self):
        self.assertTrue(client_features.prints_buyer_phone(self.CLIENT))
        for other in (None, "", "J527785",
                      "00000000-0000-0000-0000-000000000000"):
            self.assertFalse(client_features.prints_buyer_phone(other), other)

    def test_the_form_is_told_which_clients_get_the_phone_field(self):
        # The form cannot import client_features, so app.py renders the
        # answer into the page. If that context variable is dropped, Jinja
        # quietly evaluates it as false and the field never appears -- which
        # looks exactly like the client not having asked for it.
        source = io.open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        self.assertIn("buyer_phone_for_client=client_features.prints_buyer_phone",
                      source)
        form = io.open(os.path.join(ROOT, "templates", "create-invoice.html"),
                       encoding="utf-8").read()
        self.assertIn("{{ 'true' if buyer_phone_for_client else 'false' }}", form)


@unittest.skipIf(WeasyHTML is None, "WeasyPrint/pypdfium2 not installed")
class GlobalSurfacePagination(unittest.TestCase):
    """The five-row box the client asked for must fit one page.

    This template's closing block -- the PO/DC box, the amount in words,
    the summary box and the FBR marks -- breaks as one unit, so its height
    is what decides how many lines fit. Stacked rather than laid out side
    by side it came to about 92mm and exactly ONE four-line item fitted a
    page: the block jumped whole to page two and left half of page one
    blank. Nothing in the markup says so, which is why this renders real
    pages.
    """

    TEMPLATE = "invoice_global_surface.html"
    # What the client asked for: five line items on one sheet.
    REQUIRED = 5
    # What it actually takes, measured. The gap between the two is the
    # headroom, and losing it is the warning bell that the next change
    # breaks the requirement above.
    CAPACITY = 6

    # Long enough to wrap in the description column. A short description
    # fits far more rows per page and would make this pass while a real
    # invoice overflowed -- this template takes nine one-line items, six of
    # these two-line ones, and three of the four-line monsters their own
    # reference invoice carries.
    DESCRIPTION = "Hard Chromium Plating of Rotor Shaft and Sleeve No. %d"
    # The closing block's first and last pieces: if these two are not on the
    # same page, it split.
    PO_VALUE = "4500123456"
    TOTAL = "Value Inclusive of Sales Tax"
    STATIONERY = "imranelahi68@gmail.com"
    ITEMS_HEADING = "Description of Goods"

    @classmethod
    def setUpClass(cls):
        # The real mark, downscaled: the template sizes it `height: 1in;
        # width: auto`, so a broken image would collapse the row and
        # silently change the very fit being measured.
        logo = Image.open(os.path.join(ROOT, "static", "uploads", "FBR LOGO.png"))
        logo.thumbnail((240, 240))
        buffer = io.BytesIO()
        logo.save(buffer, format="PNG")
        cls.fbr_logo = ("data:image/png;base64,"
                        + base64.b64encode(buffer.getvalue()).decode())

    @staticmethod
    def flat(text):
        """Text with every space and line break removed.

        A table cell that wraps comes back from the PDF as several lines, so
        comparing without whitespace is what stops these passing vacuously.
        """
        return re.sub(r"\s+", "", text)

    def pages(self, count):
        """Text of each page of a `count`-line invoice, as rendered."""
        data = dict(
            PAYLOAD,
            items=[dict(PAYLOAD["items"][0],
                        productDescription=self.DESCRIPTION % (i + 1))
                   for i in range(count)],
            customFields=GlobalSurfaceFields.FIELDS,
        )
        markup = render(self.TEMPLATE, data, logo=self.fbr_logo)
        pdf = WeasyHTML(string=markup, base_url=ROOT).write_pdf()
        document = pypdfium2.PdfDocument(io.BytesIO(pdf))
        return [document[i].get_textpage().get_text_bounded()
                for i in range(len(document))]

    def test_the_five_items_the_client_asked_for_fit_a_single_page(self):
        # The requirement, stated on its own so a failure reads as what it
        # is rather than as a lost millimetre of headroom.
        for count in range(1, self.REQUIRED + 1):
            pages = self.pages(count)
            self.assertEqual(
                1, len(pages),
                "%d line(s) spilled onto %d pages -- the client asked for "
                "five on one sheet" % (count, len(pages)))

    def test_there_is_a_row_of_headroom_beyond_the_five(self):
        # Not a requirement, a tripwire: when this starts failing the sheet
        # is one change away from breaking the test above.
        pages = self.pages(self.CAPACITY)
        self.assertEqual(
            1, len(pages),
            "the headroom beyond the five required rows is gone -- the "
            "header, the type scale or the closing block has grown")

    def test_a_seventh_line_spills_rather_than_being_dropped(self):
        pages = self.pages(self.CAPACITY + 1)
        self.assertGreater(len(pages), 1)
        self.assertIn(self.flat(self.DESCRIPTION % (self.CAPACITY + 1)),
                      self.flat("".join(pages)),
                      "the overflowing line vanished instead of spilling")

    def test_an_overflowing_invoice_keeps_its_closing_block_together(self):
        # The failure this guards: the summary box, the Regards block and
        # the FBR marks split across a page boundary, or land alone on a
        # page, which reads as a second, empty document.
        for count in (self.CAPACITY + 1, self.CAPACITY + 4, self.CAPACITY + 12):
            pages = self.pages(count)
            carrying = [i for i, text in enumerate(pages)
                        if self.flat(self.PO_VALUE) in self.flat(text)]
            self.assertEqual([len(pages) - 1], carrying,
                             "%d lines: the PO/DC box is on pages %s"
                             % (count, carrying))
            self.assertIn(self.flat(self.TOTAL), self.flat(pages[-1]),
                          "%d lines: the summary box parted company with it"
                          % count)
            self.assertIn(self.flat(FBR_NUMBER), self.flat(pages[-1]),
                          "%d lines: the FBR marks parted company with it"
                          % count)

    def test_the_stationery_prints_on_every_page(self):
        # The footer is pre-printed paper: a second page without it is a
        # sheet that does not match the first.
        for count in (1, self.CAPACITY + 1, self.CAPACITY + 12):
            for index, text in enumerate(self.pages(count)):
                self.assertIn(self.flat(self.STATIONERY), self.flat(text),
                              "%d lines: page %d has no footer"
                              % (count, index + 1))

    def test_a_continued_items_table_repeats_its_headings(self):
        # Figures under no headings are unreadable, and worse, look like they
        # belong to whatever column the eye lands on.
        pages = self.pages(self.CAPACITY + 12)
        stem = self.flat(self.DESCRIPTION.split(" %d")[0])
        carrying = [i for i, text in enumerate(pages) if stem in self.flat(text)]
        self.assertGreater(len(carrying), 1,
                           "expected item rows on more than one page; this "
                           "test is vacuous otherwise")
        for index in carrying:
            self.assertIn(self.flat(self.ITEMS_HEADING), self.flat(pages[index]),
                          "page %d carries item rows with no headings"
                          % (index + 1))


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
        "invoice_saad_enterprises.html": "brand/3520287506167.html",
        "invoice_power_vision.html": "brand/3740545496385.html",
        "invoice_bilal_traders.html": "brand/3740549685449.html",
        "invoice_bilal_brothers.html": "brand/3740582041351.html",
        "invoice_aero_alliance.html": "brand/3740564149343.html",
        "invoice_beacon_alliance.html": "brand/3740545582835.html",
        "invoice_salon_marketings.html": "brand/3520223163127.html",
        "invoice_zahid_steel.html": "brand/J527785.html",
        "invoice_global_surface.html": "brand/global_surface.html",
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
