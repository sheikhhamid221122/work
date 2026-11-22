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
    import qrcode
    from PIL import Image
    from weasyprint import HTML as WeasyHTML
except Exception:  # pragma: no cover
    pypdfium2 = qrcode = Image = WeasyHTML = None

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
    "invoice_material_craft.html": "71a52649-1b77-4748-9a51-fd33ce621000",
    "invoice_rubberx.html": "af5d3ee4-f799-49a0-84db-813e18fdb9a7",
    "invoice_zahid_brothers.html": "74a2012a-8bf8-46e8-94fd-611e748e7c35",
}

def _dash_decimal(value):
    """'127,779.00' -> '127,779-00'. Material Craft's money notation."""
    return value.replace(".", "-")


def _whole_number(value):
    """'191.00' -> '191'. A quantity with nothing after the point."""
    return value.rstrip("0").rstrip(".") if "." in value else value


# Replicas that print a figure in their own notation rather than the
# payload's. RequiredIdentifiers checks that the figures on the face of the
# invoice are the ones in the payload; a client who writes 'Rs 1,234-56' or
# '04 each' still prints them, so the check is applied in the sheet's own
# spelling rather than waived -- a waiver would stop checking the figure at
# all. Each entry maps a label from RequiredIdentifiers.CASES to a
# re-spelling of the SAME figure; a transform that changes the number
# belongs nowhere near this table, and the integrity test below enforces
# that by comparing digits.
FIGURE_NOTATION = {
    "invoice_material_craft.html": {
        # A dash where everyone else puts a decimal point.
        "unit price": _dash_decimal,
        "value excluding tax": _dash_decimal,
        "sales tax": _dash_decimal,
        "value including tax": _dash_decimal,
        # Their sheet writes a whole quantity as an integer -- '04 each',
        # not '4.0 Numbers, pieces, units'.
        "quantity": _whole_number,
    },
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


def real_fbr_logo():
    """The Digital Invoicing mark as a data URI, downscaled.

    The pagination and letterpad tests must pass a real image: a template
    sizes the mark `height: 1in; width: auto`, and a broken one collapses
    the row, silently changing the very layout being measured.
    """
    logo = Image.open(os.path.join(ROOT, "static", "uploads", "FBR LOGO.png"))
    logo.thumbnail((240, 240))
    buffer = io.BytesIO()
    logo.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def real_qr(payload=FBR_NUMBER):
    """A real QR code as a base64 PNG, made exactly as app.py makes it.

    QR_B64 is seventeen bytes of nonsense, which is all a test asserting
    the page carries the code needs. A page being MEASURED needs more:
    a broken image renders as its alt text, and the Zahid Brothers band
    sizes the mark at 17mm, so with the fake code the band measures the
    height of one line of "FBR QR code" instead. Pagination read six
    millimetres of slack that no printed invoice has.
    """
    buffer = io.BytesIO()
    qrcode.make(payload).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


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


class MaterialCraftFields(unittest.TestCase):
    """Material Craft's four custom fields, and the two that moved.

    P.O #, P.O Date, D Challan # and DC Date fill the grid at the top
    right. Supplier # and Delivery Address began here as custom fields too
    and were moved onto the BUYER, which is what they are properties of:
    the vendor number that buyer files this client under, and the plant the
    goods go to. Both are printed-only -- app.py strips them from the FBR
    payload (`PDF_ONLY_INVOICE_FIELDS`) and nothing is derived from either.
    """

    TEMPLATE = "invoice_material_craft.html"
    CLIENT = CLIENT_TEMPLATES["invoice_material_craft.html"]

    FIELDS = [
        {"name": "P.O #", "value": "4900039374"},
        {"name": "P.O Date", "value": "19-08-2026"},
        {"name": "D Challan #", "value": "38"},
        {"name": "DC Date", "value": "20-10-2026"},
    ]
    SUPPLIER_NUMBER = "1006060"
    DELIVERY_ADDRESS = "Sahiwal Factory"

    def payload(self, **overrides):
        data = dict(PAYLOAD, customFields=self.FIELDS,
                    buyerSupplierNumber=self.SUPPLIER_NUMBER,
                    buyerDeliveryAddress=self.DELIVERY_ADDRESS)
        data.update(overrides)
        return data

    def test_all_four_custom_fields_print(self):
        markup = render(self.TEMPLATE, self.payload())
        for field in self.FIELDS:
            self.assertIn(field["name"], markup)
            self.assertIn(field["value"], markup)

    def test_the_cap_is_four_here_and_two_for_a_client_who_did_not_ask(self):
        self.assertEqual(4, client_features.custom_fields_max(self.CLIENT))
        self.assertEqual(4, client_features.custom_fields_max(None, self.CLIENT))
        for other in (None, "", "J527785", "3520223163127",
                      "00000000-0000-0000-0000-000000000000"):
            self.assertEqual(2, client_features.custom_fields_max(other), other)

    def test_a_fifth_field_is_not_printed(self):
        # Defence in depth. app.py caps the list before it gets here, but a
        # payload stored before the cap existed must not widen the sheet.
        extra = self.FIELDS + [{"name": "Gate Pass", "value": "SHOULD-NOT-PRINT"}]
        markup = render(self.TEMPLATE, self.payload(customFields=extra))
        self.assertNotIn("SHOULD-NOT-PRINT", markup)

    def test_an_empty_field_leaves_no_empty_label(self):
        # "Gate Pass" and not "P.O Date": a CSS comment IS in the rendered
        # markup, and this template's stylesheet names the four fields in
        # one, so the obvious assertion would pass vacuously.
        fields = [self.FIELDS[0], {"name": "Gate Pass", "value": ""}]
        markup = render(self.TEMPLATE, self.payload(customFields=fields))
        self.assertIn("P.O #", markup)
        self.assertNotIn("Gate Pass", markup)

    # ---- the two fields that live on the buyer --------------------------

    def test_the_supplier_number_and_delivery_address_come_from_the_buyer(self):
        markup = render(self.TEMPLATE, self.payload())
        self.assertIn(self.SUPPLIER_NUMBER, markup)
        self.assertIn(self.DELIVERY_ADDRESS, markup)

    def test_the_supplier_number_is_not_hardcoded_to_one_buyer(self):
        # It was, in the first build. Their two sheets carry two different
        # numbers -- it is the vendor number the BUYER files them under, and
        # a wrong one is the number that buyer pays against.
        markup = render(self.TEMPLATE, self.payload(buyerSupplierNumber="0000332375"))
        self.assertIn("0000332375", markup)
        self.assertNotIn(self.SUPPLIER_NUMBER, markup)

    def test_both_rows_are_ruled_even_when_the_buyer_has_neither(self):
        # Their sheet rules three rows in each box whatever is filled in.
        markup = render(self.TEMPLATE, self.payload(
            buyerSupplierNumber="", buyerDeliveryAddress=""))
        self.assertIn("Delivery Address", markup)
        self.assertIn("Supplier #", markup)
        self.assertNotIn(self.DELIVERY_ADDRESS, markup)

    def test_no_other_replica_prints_either_of_them(self):
        # A client who did not ask for them must not get them, whatever the
        # payload happens to carry.
        data = dict(PAYLOAD, buyerSupplierNumber=self.SUPPLIER_NUMBER,
                    buyerDeliveryAddress=self.DELIVERY_ADDRESS)
        for template in set(CLIENT_TEMPLATES) - {self.TEMPLATE}:
            markup = render(template, data)
            self.assertNotIn(self.SUPPLIER_NUMBER, markup, template)
            self.assertNotIn(self.DELIVERY_ADDRESS, markup, template)

    def test_only_this_client_is_offered_the_two_buyer_fields(self):
        self.assertTrue(client_features.prints_buyer_supplier_fields(self.CLIENT))
        for other in (None, "", "J527785",
                      CLIENT_TEMPLATES["invoice_global_surface.html"],
                      "00000000-0000-0000-0000-000000000000"):
            self.assertFalse(
                client_features.prints_buyer_supplier_fields(other), other)

    def test_neither_field_is_ever_filed(self):
        # The pair are printed-only. If they were not in this tuple they
        # would be sent to FBR, whose invoice schema has no such element.
        import app as application
        self.assertIn("buyerSupplierNumber", application.PDF_ONLY_INVOICE_FIELDS)
        self.assertIn("buyerDeliveryAddress", application.PDF_ONLY_INVOICE_FIELDS)

    def test_the_form_is_told_which_clients_get_the_two_fields(self):
        # The form cannot import client_features, so app.py renders the
        # answer into the page. If that context variable is dropped, Jinja
        # quietly evaluates it as false and the fields never appear -- which
        # looks exactly like the client not having asked for them.
        source = io.open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        self.assertIn(
            "buyer_supplier_fields_for_client=client_features.prints_buyer_supplier_fields",
            source)
        form = io.open(os.path.join(ROOT, "templates", "create-invoice.html"),
                       encoding="utf-8").read()
        self.assertIn("{{ 'true' if buyer_supplier_fields_for_client else 'false' }}", form)

    # ---- the table ------------------------------------------------------

    def test_the_table_is_their_six_columns(self):
        # Their second sheet added an HS Code column, which is why this
        # template no longer waives the HS code in RequiredIdentifiers.
        rows = items_table_rows(render(self.TEMPLATE, self.payload()))
        self.assertEqual({6}, set(rows))
        markup = render(self.TEMPLATE, self.payload())
        for heading in ("S.No", "Description", "HS Code", "Qty",
                        "Unit Price", "Amount"):
            self.assertIn(">%s<" % heading, markup)

    def test_the_quantity_prints_the_way_their_sheet_writes_it(self):
        # '04 each', not '4.0 Numbers, pieces, units'. The long FBR unit
        # name wrapped to four lines in this column and is what pushed
        # their six-line invoice onto a second page.
        data = self.payload(items=[dict(PAYLOAD["items"][0], quantity=4.0,
                                        uoM="Numbers, pieces, units")])
        markup = render(self.TEMPLATE, data)
        self.assertIn("04 each", markup)
        self.assertNotIn("Numbers, pieces, units", markup)

    def test_a_fractional_quantity_is_left_exactly_as_it_came(self):
        # Rounding a quantity would change what the sheet says was supplied.
        data = self.payload(items=[dict(PAYLOAD["items"][0], quantity="12.5",
                                        uoM="Kilogram")])
        markup = render(self.TEMPLATE, data)
        self.assertIn("12.5 Kilogram", markup)

    def test_every_other_unit_prints_as_fbr_spells_it(self):
        data = self.payload(items=[dict(PAYLOAD["items"][0], quantity=3,
                                        uoM="Square Metre")])
        self.assertIn("03 Square Metre", render(self.TEMPLATE, data))

    def test_money_is_written_in_their_dash_notation(self):
        # 'Rs 102,000-00', not 'Rs 102,000.00'. RequiredIdentifiers checks
        # the figures themselves through FIGURE_NOTATION; this pins the
        # notation, which is the part a future tidy-up would "fix".
        markup = render(self.TEMPLATE, self.payload())
        self.assertIn("Rs 127,779-00", markup)
        self.assertNotIn("Rs 127,779.00", markup)

    def test_the_amount_in_words_is_phrased_the_way_their_sheet_phrases_it(self):
        # Theirs carries no joining "and" and no hyphens, and no currency
        # tail after the figure -- the label in front of it says Rupees.
        data = self.payload(amountInWords=(
            "one million, one hundred and sixty-one thousand, one hundred "
            "and twenty rupees, zero paisa only"))
        markup = render(self.TEMPLATE, data)
        self.assertIn(
            "Rupees: One million one hundred sixty one thousand one hundred "
            "twenty only.", markup)

    def test_a_non_zero_paisa_amount_is_not_left_dangling(self):
        data = self.payload(amountInWords="nine hundred rupees, fifty paisa only")
        self.assertIn("Rupees: Nine hundred and fifty paisa only.",
                      render(self.TEMPLATE, data))

    # ---- the seller block ------------------------------------------------

    def test_the_cnic_row_their_second_sheet_dropped_is_gone(self):
        markup = render(self.TEMPLATE, self.payload())
        self.assertNotIn("CNIC", markup)

    def test_the_bare_ntn_prints_with_its_check_digit(self):
        # The client asked for 1270230-7 while their profile holds the bare
        # seven characters. The digit is derived from them, so the sheet and
        # the filing state the same number.
        markup = render(self.TEMPLATE, self.payload(sellerNTNCNIC="1270230"))
        self.assertIn("1270230-7", markup)

    def test_an_ntn_that_already_carries_it_is_left_alone(self):
        markup = render(self.TEMPLATE, self.payload(sellerNTNCNIC="1270230-7"))
        self.assertIn("1270230-7", markup)
        self.assertNotIn("1270230-7-7", markup)

    def test_no_other_sellers_ntn_is_ever_rewritten(self):
        markup = render(self.TEMPLATE, self.payload(sellerNTNCNIC="1203281-6"))
        self.assertIn("1203281-6", markup)
        self.assertNotIn("1270230-7", markup)

    def test_their_gst_number_prints_in_the_grouping_their_sheet_uses(self):
        markup = render(self.TEMPLATE, self.payload(sellerSTRN="0300127023012"))
        self.assertIn("03-00-1270-230-12", markup)
        self.assertNotIn(">0300127023012<", markup)

    def test_no_other_sellers_gst_number_is_ever_regrouped(self):
        markup = render(self.TEMPLATE, self.payload(sellerSTRN="3520224169621"))
        self.assertIn("3520224169621", markup)
        self.assertNotIn("03-00-1270-230-12", markup)

    def test_the_seller_is_named_from_the_payload(self):
        # Their masthead is blank until the sheet meets their letterpad, so
        # the sign-off is what names the seller on the PDF itself.
        self.assertIn("For Apple International,", render(self.TEMPLATE))


@unittest.skipIf(WeasyHTML is None, "WeasyPrint/pypdfium2 not installed")
class MaterialCraftLetterpad(unittest.TestCase):
    """The two bands that must stay empty, because their paper fills them.

    This client prints onto pre-printed stationery. Their masthead ink runs
    to 16.2mm from the top of the sheet and their footer ink starts at
    276.3mm; anything rendered into either band prints over their own logo
    or their own telephone number. Margins in the stylesheet are the
    mechanism, but asserting on the CSS proves nothing about where the ink
    actually lands -- so this renders real pages and looks at the pixels.
    """

    TEMPLATE = "invoice_material_craft.html"

    # Measured off their sheet. The reserve has to cover at least these.
    THEIR_MASTHEAD_ENDS = 16.2
    THEIR_FOOTER_STARTS = 276.3
    # What the template actually reserves.
    TOP_RESERVE = 21.0
    BOTTOM_RESERVE = 23.0
    PX_PER_MM = 10

    @classmethod
    def setUpClass(cls):
        cls.fbr_logo = real_fbr_logo()

    def ink_rows(self, count=1, description="Cutter HSS W20 No. %d"):
        """For each page, the mm positions of the topmost and lowest ink."""
        data = dict(
            PAYLOAD,
            items=[dict(PAYLOAD["items"][0], productDescription=description % (i + 1))
                   for i in range(count)],
            customFields=MaterialCraftFields.FIELDS,
        )
        markup = render(self.TEMPLATE, data, logo=self.fbr_logo)
        pdf = WeasyHTML(string=markup, base_url=ROOT).write_pdf()
        document = pypdfium2.PdfDocument(io.BytesIO(pdf))
        spans = []
        for index in range(len(document)):
            page = document[index].render(scale=self.PX_PER_MM * 25.4 / 72.0)
            pixels = page.to_pil().convert("L")
            self.assertEqual(297, round(pixels.height / self.PX_PER_MM),
                             "page %d is not A4" % (index + 1))
            inked = [y for y in range(pixels.height)
                     if min(pixels.crop((0, y, pixels.width, y + 1)).getdata()) < 200]
            self.assertTrue(inked, "page %d is blank" % (index + 1))
            spans.append((inked[0] / self.PX_PER_MM, inked[-1] / self.PX_PER_MM))
        return spans

    def test_the_reserve_covers_their_own_letterpad(self):
        # The template's margins against the ink measured on their sheet.
        # If their stationery is ever re-measured, this is the comparison
        # that has to be redone -- not the margins on their own.
        self.assertGreaterEqual(self.TOP_RESERVE, self.THEIR_MASTHEAD_ENDS)
        self.assertGreaterEqual(297 - self.BOTTOM_RESERVE, 0)
        self.assertLessEqual(297 - self.BOTTOM_RESERVE, self.THEIR_FOOTER_STARTS)

    def test_no_ink_reaches_either_band_on_any_page(self):
        # The assertion this class exists for. One line item, a boxful and
        # an invoice that spills: the bands stay clear on every page of all
        # three, including the continuation page, which gets the same
        # pre-printed paper.
        for count in (1, 7, 14, 30):
            for index, (top, bottom) in enumerate(self.ink_rows(count)):
                self.assertGreaterEqual(
                    top, self.TOP_RESERVE - 0.5,
                    "%d lines, page %d: ink at %.1fmm is inside the %gmm "
                    "reserved for their masthead"
                    % (count, index + 1, top, self.TOP_RESERVE))
                self.assertLessEqual(
                    bottom, 297 - self.BOTTOM_RESERVE + 0.5,
                    "%d lines, page %d: ink at %.1fmm is inside the %gmm "
                    "reserved for their footer"
                    % (count, index + 1, bottom, self.BOTTOM_RESERVE))

    def test_nothing_of_their_stationery_is_printed(self):
        # The sheet must not duplicate what their paper already carries: a
        # rendered telephone number lands on top of the printed one.
        markup = render(self.TEMPLATE, dict(PAYLOAD, customFields=MaterialCraftFields.FIELDS))
        for printed in ("0333-4243778", "materialcraft@gmail.com",
                        "Bahria Orchard", "212/35"):
            self.assertNotIn(printed, markup, printed)
        # Nor the seller's address, which is their paper's job and would
        # change shape with the payload if it were interpolated.
        self.assertNotIn(PAYLOAD["sellerAddress"], markup)


@unittest.skipIf(WeasyHTML is None, "WeasyPrint/pypdfium2 not installed")
class MaterialCraftIdentifierBoxes(unittest.TestCase):
    """The two ruled boxes in each banded row must stay two boxes.

    Their sheet puts the buyer block at 24.1-87.2mm and the invoice grid
    at 135.5-191.8mm, with ~48mm of paper between them, and does the same
    again with the two identifier boxes underneath. Both rows are laid out
    by a three-cell table whose middle cell is empty.

    That empty cell is the whole risk. Under the browser's default auto
    table layout a percentage width is a hint, every cell is given its
    content's minimum first, and an empty cell's minimum is zero -- so
    when the boxes hold something wide the gap is what gets squeezed, and
    the two boxes print touching as one merged table. It shipped that way:
    the width rules were scoped to the first row's class, the second row
    had no declared widths at all, and short test values hid it until a
    buyer with no delivery address and a 13-digit seller NTN came along.

    Nothing about that is visible in the markup, which is why this
    rasterises the page and looks at where the ink actually falls.
    """

    TEMPLATE = "invoice_material_craft.html"
    PX_PER_MM = 10

    # The content box: their boxes must start and end on these.
    LEFT_EDGE = 24.0
    RIGHT_EDGE = 192.0
    # The two banded rows, as mm windows down the page.
    BANDS = {"buyer block and invoice grid": (33.5, 66.0),
             "client and seller identifiers": (68.5, 91.0)}

    @classmethod
    def setUpClass(cls):
        cls.fbr_logo = real_fbr_logo()

    def ink_columns(self, band, data):
        """The runs of inked columns in a horizontal band, in mm."""
        markup = render(self.TEMPLATE, data, logo=self.fbr_logo)
        pdf = WeasyHTML(string=markup, base_url=ROOT).write_pdf()
        document = pypdfium2.PdfDocument(io.BytesIO(pdf))
        page = document[0].render(scale=self.PX_PER_MM * 25.4 / 72.0)
        pixels = page.to_pil().convert("L")
        top, bottom = (int(edge * self.PX_PER_MM) for edge in band)
        strip = pixels.crop((0, top, pixels.width, bottom))
        inked = [x for x in range(strip.width)
                 if min(strip.crop((x, 0, x + 1, strip.height)).getdata()) < 200]
        runs, start, previous = [], inked[0], inked[0]
        for x in inked[1:]:
            # 5mm of clear paper ends a run; a cell rule is 1px.
            if x - previous > 5 * self.PX_PER_MM:
                runs.append((start / self.PX_PER_MM, previous / self.PX_PER_MM))
                start = x
            previous = x
        runs.append((start / self.PX_PER_MM, previous / self.PX_PER_MM))
        return runs

    def worst_case(self):
        """The payload that broke it: both optional rows empty, long NTN.

        An empty cell is what lets the gap collapse, and the 13-digit
        seller NTN is what makes the boxes want the room.
        """
        return dict(PAYLOAD,
                    customFields=MaterialCraftFields.FIELDS,
                    sellerNTNCNIC="3520229157309",
                    buyerSupplierNumber="",
                    buyerDeliveryAddress="")

    def test_each_banded_row_is_two_boxes_with_paper_between_them(self):
        for label, band in self.BANDS.items():
            runs = self.ink_columns(band, self.worst_case())
            self.assertEqual(
                2, len(runs),
                "%s: expected two separate boxes, got %s -- the gap between "
                "them has collapsed and they print as one table"
                % (label, ["%.1f-%.1f" % r for r in runs]))
            (left_start, left_end), (right_start, right_end) = runs
            self.assertGreaterEqual(
                right_start - left_end, 40.0,
                "%s: only %.1fmm of paper between the boxes; their sheet "
                "leaves about 48" % (label, right_start - left_end))

    def test_neither_box_runs_past_the_margins(self):
        # The other way this goes wrong: a layout fix that makes the table
        # wider than its container pushes the right-hand box into the
        # margin, which on this sheet is their pre-printed letterpad.
        for label, band in self.BANDS.items():
            runs = self.ink_columns(band, self.worst_case())
            self.assertGreaterEqual(runs[0][0], self.LEFT_EDGE - 0.5,
                                    "%s: ink left of the content box" % label)
            self.assertLessEqual(
                runs[-1][1], self.RIGHT_EDGE + 0.5,
                "%s: ink at %.1fmm is past the %gmm right margin"
                % (label, runs[-1][1], self.RIGHT_EDGE))

    def test_the_two_rows_line_up_with_each_other(self):
        # Their sheet rules both rows on the same two verticals. A row that
        # sizes itself by content drifts out of line with the one above.
        measured = [self.ink_columns(band, self.worst_case())
                    for band in self.BANDS.values()]
        for index, edge in enumerate(("left box start", "left box end",
                                      "right box start", "right box end")):
            values = [run[index % 2] for runs in measured
                      for run in [runs[index // 2]]]
            self.assertAlmostEqual(
                values[0], values[1], delta=1.0,
                msg="%s differs between the two rows: %.1f vs %.1f"
                    % (edge, values[0], values[1]))


@unittest.skipIf(WeasyHTML is None, "WeasyPrint/pypdfium2 not installed")
class MaterialCraftPagination(unittest.TestCase):
    """How many line items reach the buyer on one sheet.

    The client's words were that he has more products than the one on the
    sample and the invoice must not run to two pages. That is a measured
    quantity, not a wish: the letterpad reserve takes 42mm off the page
    before anything is laid out, their header matter another 99mm, and the
    closing block breaks as one unit. The numbers below were measured and
    are pinned so a later change to any of those three reads as what it is.
    """

    TEMPLATE = "invoice_material_craft.html"

    # A description longer than any on either of their sheets, so it wraps
    # to two lines in the Description column. A one-word description fits
    # far more rows and would make this pass while a real invoice
    # overflowed -- which is exactly what the first build did.
    WRAPPING = ("Supply of reconditioned Jaw bottom assembly 5940383-2664 LD "
                "A.P.S MW1200 with hardened shank no. %d")
    # What the client can count on: six of those. Their own invoices run to
    # six items, all of which fit on one line and twelve of which fit a
    # page, so this is already past anything they have sent.
    REQUIRED = 6
    # What it actually takes, measured. The gap between the two numbers is
    # the headroom, and losing it is the warning bell that the next change
    # breaks the promise above rather than the promise breaking silently.
    #
    # This was 7/8 until the top margin went from 19mm to 21mm, at the
    # client's request, so their logo clears the content when the sheet
    # feeds through their printer. 2mm is most of a row at 9pt and it cost
    # exactly one -- but only of a description longer than any on either of
    # their sheets. Their own still fit twelve to a page.
    WRAPPING_CAPACITY = 7
    # One of their own descriptions, which fits a single line at 9pt.
    SHORT = "Supply of Flight through knife 5940383-2659 LD A.P.S no. %d"
    SHORT_CAPACITY = 12

    NOTE = "system generated invoice"
    TOTAL = "Gross Amount"
    ITEMS_HEADING = "Description"

    @classmethod
    def setUpClass(cls):
        cls.fbr_logo = real_fbr_logo()

    @staticmethod
    def flat(text):
        """Text with every space and line break removed.

        A wrapped table cell comes back from the PDF as several lines, so
        comparing without whitespace is what stops these passing vacuously.
        """
        return re.sub(r"\s+", "", text)

    def pages(self, count, description):
        # The long FBR unit name on every line, because shortening it to
        # "each" is what bought most of this capacity and a test that never
        # exercises it would not notice the shortening being lost.
        data = dict(
            PAYLOAD,
            items=[dict(PAYLOAD["items"][0],
                        productDescription=description % (i + 1),
                        quantity=4.0, uoM="Numbers, pieces, units")
                   for i in range(count)],
            customFields=MaterialCraftFields.FIELDS,
            buyerSupplierNumber=MaterialCraftFields.SUPPLIER_NUMBER,
            buyerDeliveryAddress=MaterialCraftFields.DELIVERY_ADDRESS,
        )
        markup = render(self.TEMPLATE, data, logo=self.fbr_logo)
        pdf = WeasyHTML(string=markup, base_url=ROOT).write_pdf()
        document = pypdfium2.PdfDocument(io.BytesIO(pdf))
        return [document[i].get_textpage().get_text_bounded()
                for i in range(len(document))]

    def test_the_items_the_client_can_count_on_fit_one_sheet(self):
        # The requirement, stated on its own so a failure reads as what it
        # is rather than as a lost millimetre of headroom.
        for count in range(1, self.REQUIRED + 1):
            pages = self.pages(count, self.WRAPPING)
            self.assertEqual(
                1, len(pages),
                "%d two-line item(s) spilled onto %d pages -- the header, "
                "the type scale or the closing block has grown"
                % (count, len(pages)))

    def test_there_is_a_row_of_headroom_beyond_that(self):
        # Not a requirement, a tripwire: when this starts failing the sheet
        # is one change away from breaking the test above.
        pages = self.pages(self.WRAPPING_CAPACITY, self.WRAPPING)
        self.assertEqual(
            1, len(pages),
            "the headroom beyond the %d required rows is gone -- the "
            "letterpad reserve, the header matter or the closing block "
            "has grown" % self.REQUIRED)

    def test_their_own_single_line_descriptions_fit_twelve_to_a_sheet(self):
        pages = self.pages(self.SHORT_CAPACITY, self.SHORT)
        self.assertEqual(1, len(pages),
                         "%d one-line items spilled onto %d pages"
                         % (self.SHORT_CAPACITY, len(pages)))

    def test_an_overflowing_line_spills_rather_than_vanishing(self):
        for capacity, description in ((self.WRAPPING_CAPACITY, self.WRAPPING),
                                      (self.SHORT_CAPACITY, self.SHORT)):
            pages = self.pages(capacity + 1, description)
            self.assertGreater(len(pages), 1)
            self.assertIn(self.flat(description % (capacity + 1)),
                          self.flat("".join(pages)),
                          "the overflowing line vanished instead of spilling")

    def test_an_overflowing_invoice_keeps_its_closing_block_together(self):
        # The failure this guards: the sign-off, the FBR marks and the
        # closing note split across a page boundary, so the QR code and the
        # number it belongs to end up on different sheets.
        for count in (self.SHORT_CAPACITY + 1, 20, 30):
            pages = self.pages(count, self.SHORT)
            carrying = [i for i, text in enumerate(pages)
                        if self.flat(self.NOTE) in self.flat(text)]
            self.assertEqual([len(pages) - 1], carrying,
                             "%d lines: the closing note is on pages %s"
                             % (count, carrying))
            self.assertIn(self.flat(FBR_NUMBER), self.flat(pages[-1]),
                          "%d lines: the FBR marks left the closing block"
                          % count)

    def test_a_continued_items_table_repeats_its_headings(self):
        # Figures under no headings are unreadable, and worse, look like
        # they belong to whatever column the eye lands on.
        pages = self.pages(30, self.SHORT)
        stem = self.flat(self.SHORT.split(" no. %d")[0])
        carrying = [i for i, text in enumerate(pages) if stem in self.flat(text)]
        self.assertGreater(len(carrying), 1,
                           "expected item rows on more than one page; this "
                           "test is vacuous otherwise")
        for index in carrying:
            self.assertIn(self.flat(self.ITEMS_HEADING), self.flat(pages[index]),
                          "page %d carries item rows with no headings"
                          % (index + 1))

    def test_the_totals_follow_the_last_line_item(self):
        # The totals belong with the table, not stranded ahead of it.
        for count, description in ((1, self.SHORT), (30, self.SHORT),
                                   (8, self.WRAPPING)):
            pages = self.pages(count, description)
            carrying = [i for i, text in enumerate(pages)
                        if self.flat(self.TOTAL) in self.flat(text)]
            self.assertEqual(1, len(carrying),
                             "%d lines: the totals are on pages %s"
                             % (count, carrying))


@unittest.skipIf(WeasyHTML is None, "WeasyPrint/pypdfium2 not installed")
class RubberXPagination(unittest.TestCase):
    """Six line items fit one page, a seventh spills, and the foot stays whole.

    Rubber X's sheet is F.K. Printers' arrangement with an extra column and a
    services footer fixed in a 22 mm bottom margin -- 12 mm more than F.K.
    needs. That margin is the whole difference, and it comes straight out of
    the items box, so anything that grows the header or the type scale costs
    a row here before it costs one anywhere else.

    The failure this exists for actually happened: the first cut put the FBR
    stamp, the amount in words and the totals at the foot of page 1 and left
    the sign-off line alone at the top of page 2, which reads as a second,
    empty document. Same trap as ZahidSteelPagination above, and pinned the same
    way -- on real pages, because markup cannot answer "does it fit".
    """

    TEMPLATE = "invoice_rubberx.html"

    # Measured, not chosen, and measured per description depth -- capacity
    # on this sheet is a function of how deep a line prints, not of a row
    # count. A one-word description fits twice as many rows as their own
    # longest one and would let a test pass while a real invoice overflowed,
    # so each of these is checked with text that actually sets that depth.
    #
    #   DESCRIPTION is their own longest, which sets three printed lines in
    #   a 21% column; DEEP sets six, which is deeper than anything on either
    #   of their sheets and is here as the pessimistic end.
    DESCRIPTION = "CHAIN SOLID ROLLER RC50 SS GERMANY/JAPAN %d"
    CAPACITY = 6

    DEEP = ("CHAIN SOLID ROLLER RC50 SS GERMANY/JAPAN CONSIGNMENT "
            "LINE ITEM EXTRA LONG DETAIL %d")
    DEEP_CAPACITY = 4

    SHALLOW = "RUBBER SHEET %d"
    SHALLOW_CAPACITY = 12

    WORDS = "Rupees Only"
    TOTAL = "Sales Tax Inclusive Value"
    SIGNATURE = "Muhamad Basit / Owner"
    SERVICES = "General order supplier"
    ITEMS_HEADING = "Value Incl."

    @classmethod
    def setUpClass(cls):
        # The real mark, downscaled: the template sizes it `width: 26mm;
        # height: auto`, so a broken image would collapse the row and
        # silently change the fit.
        logo = Image.open(os.path.join(ROOT, "static", "uploads", "FBR LOGO.png"))
        logo.thumbnail((240, 240))
        buffer = io.BytesIO()
        logo.save(buffer, format="PNG")
        cls.fbr_logo = ("data:image/png;base64,"
                        + base64.b64encode(buffer.getvalue()).decode())

    @staticmethod
    def flat(text):
        """Text with every space and line break removed.

        A wrapped table cell comes back from the PDF as several lines, so a
        wrapped description never matches the string that produced it.
        """
        return re.sub(r"\s+", "", text)

    def markup(self, count, description=None):
        data = dict(PAYLOAD, items=[
            dict(PAYLOAD["items"][0],
                 productDescription=(description or self.DESCRIPTION) % (i + 1))
            for i in range(count)
        ])
        return render(self.TEMPLATE, data, logo=self.fbr_logo)

    @staticmethod
    def paginate(markup):
        pdf = WeasyHTML(string=markup, base_url=ROOT).write_pdf()
        document = pypdfium2.PdfDocument(io.BytesIO(pdf))
        return [document[i].get_textpage().get_text_bounded()
                for i in range(len(document))]

    def pages(self, count, description=None):
        """Text of each page of a `count`-line invoice, as rendered."""
        return self.paginate(self.markup(count, description))

    def pages_unpadded(self, count, description=None):
        """The same invoice with the filler rows taken back out.

        Rendering it twice is what makes the next test mean something: the
        question is not "does this fit" but "did the padding change the
        answer", and only the same sheet without its blank rows can say.
        """
        stripped = re.sub(r"<tr>\s*(?:<td class=\"filler\">[^<]*</td>\s*)+</tr>",
                          "", self.markup(count, description))
        self.assertNotIn('class="filler"', stripped,
                         "the filler rows did not match the pattern that "
                         "strips them; this test would be vacuous")
        return self.paginate(stripped)

    def test_each_measured_capacity_still_fits_one_page(self):
        for description, capacity in ((self.DESCRIPTION, self.CAPACITY),
                                      (self.DEEP, self.DEEP_CAPACITY),
                                      (self.SHALLOW, self.SHALLOW_CAPACITY)):
            for count in range(1, capacity + 1):
                pages = self.pages(count, description)
                self.assertEqual(
                    1, len(pages),
                    "%d line(s) of %r spilled onto %d pages -- the masthead, "
                    "the type scale or the bottom margin has grown and eaten "
                    "a row" % (count, description, len(pages)))

    def test_the_filler_rows_are_never_what_overflows_the_page(self):
        # The budget pads a short invoice out to the depth of their printed
        # box. It must do that only in space the page already had: a sheet
        # that fits must not stop fitting because blank rows were added
        # under it. This is the bug that was actually shipped once -- three
        # four-line items were charged a flat 2 units each and padded with
        # six more rows, and the sheet ran to two pages.
        for description in (self.DESCRIPTION, self.DEEP, self.SHALLOW):
            for count in range(1, self.SHALLOW_CAPACITY + 1):
                if len(self.pages(count, description)) == 1:
                    continue
                self.assertGreater(
                    len(self.pages_unpadded(count, description)), 1,
                    "%d line(s) of %r fit on their own but spill once the "
                    "filler rows are added" % (count, description))

    def test_a_line_past_capacity_spills_rather_than_being_dropped(self):
        for description, capacity in ((self.DESCRIPTION, self.CAPACITY),
                                      (self.DEEP, self.DEEP_CAPACITY)):
            over = capacity + 1
            pages = self.pages(over, description)
            self.assertGreater(len(pages), 1,
                               "%r: %d lines still fit one page, so the "
                               "capacity recorded here is stale"
                               % (description, over))
            self.assertIn(self.flat(description % over),
                          self.flat("".join(pages)),
                          "the overflowing line vanished instead of spilling")

    def test_an_overflowing_invoice_keeps_its_closing_block_together(self):
        # The signature rules must not part company with the totals, the
        # amount in words and the FBR stamp. All four are one block.
        for count in (self.CAPACITY + 1, self.CAPACITY + 3, self.CAPACITY + 10):
            pages = self.pages(count)
            last = self.flat(pages[-1])
            # NOT the FBR invoice number: it prints once, with the
            # other identifiers at the top of page 1. What belongs with the
            # closing block is the QR and the Digital Invoicing logo.
            for piece in (self.SIGNATURE, self.TOTAL, self.WORDS, "FBR QR code"):
                self.assertIn(self.flat(piece), last,
                              "%d lines: %r is not on the closing page"
                              % (count, piece))
            earlier = self.flat("".join(pages[:-1]))
            self.assertNotIn(self.flat(self.SIGNATURE), earlier,
                             "%d lines: the signature block is on two pages"
                             % count)

    def test_the_services_footer_prints_on_every_page(self):
        # It stands in for the pre-printed band on their stationery: a second
        # page without it is a sheet that does not match the first.
        for count in (1, self.CAPACITY + 1, self.CAPACITY + 10):
            for index, text in enumerate(self.pages(count)):
                self.assertIn(self.flat(self.SERVICES), self.flat(text),
                              "%d lines: page %d has no services footer"
                              % (count, index + 1))

    def test_a_continued_items_table_repeats_its_headings(self):
        # Figures under no headings are unreadable, and worse, look like they
        # belong to whatever column the eye lands on.
        pages = self.pages(self.CAPACITY + 10, self.DEEP)
        stem = self.flat(self.DEEP.split(" CONSIGNMENT")[0])
        carrying = [i for i, text in enumerate(pages) if stem in self.flat(text)]
        self.assertGreater(len(carrying), 1,
                           "expected item rows on more than one page; this "
                           "test is vacuous otherwise")
        for index in carrying:
            self.assertIn(self.flat(self.ITEMS_HEADING), self.flat(pages[index]),
                          "page %d carries item rows with no headings"
                          % (index + 1))


class ZahidBrothersSheet(unittest.TestCase):
    """Their Excel form: thirteen measured columns and four client rules.

    The columns are the point of this template -- the client asked for the
    items table at exactly the size they set and for everything else to be
    fitted around it -- so the tests here are about the table, and about the
    rules they gave for what goes in it.
    """

    TEMPLATE = "invoice_zahid_brothers.html"

    HEADINGS = [
        "Sr.No.", "HS CODE", "HS CODE DESCRIPTION", "Item Type",
        "Product Code &amp; Description", "Qty", "UOM", "Part Unit",
        "Unit Rate", "Gross Amount", "Sales Tax",
        "Further Sales Tax if ANY", "Value Including Sales Tax",
    ]

    # The columns, in millimetres. These were once measured off the vertical
    # rules in their own PDF, because the client asked for the table at
    # exactly the size they set. They then asked for the invoice to look
    # modern, which replaced that ask.
    #
    # They are now sized by MEASUREMENT rather than proportion: each is at
    # least as wide as its widest unbreakable content rendered in DejaVu
    # Sans, the face the server falls back to, plus a margin. The previous
    # set was proportioned against Segoe UI, which this machine has and the
    # server does not, and the client's own sheet came back with the HS code
    # and the Sales Tax total each split over two lines.
    # test_nothing_wraps_in_the_font_the_server_actually_has is what holds
    # that now; this list holds the shape of the table.
    WIDTHS = ["9.3mm", "15.4mm", "18.5mm", "13.4mm", "16.5mm", "12.3mm",
              "9.0mm", "10.5mm", "14.9mm", "18.4mm", "18.4mm", "11.1mm",
              "18.3mm"]

    # What the client asked to print the same whatever the business profile
    # says. See the note on RequiredIdentifiers.FIXED.
    PHONE = "0542-450989"

    def line(self, **overrides):
        item = dict(PAYLOAD["items"][0])
        item.update(overrides)
        return dict(PAYLOAD, items=[item])

    def test_the_table_is_their_thirteen_columns(self):
        markup = render(self.TEMPLATE)
        for heading in self.HEADINGS:
            self.assertIn(heading, markup, heading)
        for width in self.WIDTHS:
            self.assertIn("width:" + width, markup, width)

    def test_the_columns_add_up_to_the_width_of_the_page(self):
        # 186 mm: A4 less the 12 mm margins. One column widened without the
        # others moving would pass every other test here and still push the
        # table off the page.
        total = sum(float(w[:-2]) for w in self.WIDTHS)
        self.assertAlmostEqual(186.0, total, places=1)
        self.assertIn("width: 186mm", render(self.TEMPLATE))

    def test_the_two_figures_under_the_title_are_labelled(self):
        # Unlabelled they read as two stray figures under the heading.
        markup = render(self.TEMPLATE)
        self.assertIn("Invoice Number", markup)
        self.assertIn("Invoice Date", markup)
        # And the strapline the client asked to lose. Checked by its class,
        # not its words: "FBR Digital Invoicing System" is also the alt text
        # on the compliance mark at the foot, which stays.
        self.assertNotIn('class="doc-sub"', markup)

    def test_the_address_is_printed_once(self):
        # It was under the mark AND the first line of the Supplier card.
        data = self.line()
        data["sellerAddress"] = "IKHLAS PUR ROAD, SHAKARGARH"
        markup = render(self.TEMPLATE, data)
        self.assertEqual(1, markup.count("IKHLAS PUR ROAD"))

    def test_the_two_cards_stay_inside_the_page(self):
        # Their right edge is the table's right edge; a card wider than that
        # prints over the margin, which is what border-spacing did.
        markup = render(self.TEMPLATE)
        page = WeasyHTML(string=markup, base_url=ROOT).render().pages[0]

        def walk(box):
            yield box
            for child in getattr(box, "children", ()) or ():
                for item in walk(child):
                    yield item

        px_mm = 25.4 / 96.0
        right_edges = []
        for box in walk(page._page_box):
            if getattr(box, "element_tag", None) != "table":
                continue
            # Every table appears twice in the box tree -- the wrapper that
            # holds the caption and the table itself -- and only the second
            # carries the width that is drawn.
            name = type(box).__name__
            if "Table" not in name or "Wrapper" in name:
                continue
            try:
                classes = (box.element.get("class") or "").split()
            except Exception:
                continue
            if "parties" in classes or "items" in classes:
                right_edges.append(
                    round((box.position_x + box.width) * px_mm, 1))
        self.assertEqual(2, len(right_edges), "cards and items table")
        self.assertAlmostEqual(right_edges[0], right_edges[1], delta=0.3)
        # A4 less the 12 mm right margin.
        self.assertLessEqual(max(right_edges), 198.0)

    def test_the_total_is_spelled_out(self):
        markup = render(self.TEMPLATE)
        self.assertIn("Amount in words", markup)
        self.assertIn(PAYLOAD["amountInWords"], markup)

    def test_there_is_nothing_to_spell_before_it_is_totalled(self):
        # The row is left off rather than printing an empty rule.
        data = dict(PAYLOAD)
        data.pop("amountInWords", None)
        self.assertNotIn("Amount in words", render(self.TEMPLATE, data))

    def test_the_phone_ntn_and_registration_number_are_the_pinned_ones(self):
        # The payload carries Apple International's seller, so a template
        # reading these off it would print those instead.
        markup = render(self.TEMPLATE)
        for value in (self.PHONE, "1472236-4", "3520267684365"):
            self.assertIn(value, markup, value)
        self.assertNotIn(PAYLOAD["sellerNTNCNIC"], markup)
        # Not a whole-document search for the payload's STRN: the FBR
        # invoice number begins with it, and that one is not the seller's
        # registration printed as a field. Check the two rows instead.
        ntn_row = markup.split("NTN No.")[1].split("</tr>")[0]
        self.assertIn("1472236-4", ntn_row)
        strn_row = markup.split("Sales Tax Registration No.")[1].split("</tr>")[0]
        self.assertIn("3520267684365", strn_row)
        self.assertNotIn(PAYLOAD["sellerSTRN"], strn_row)

    def test_the_seller_name_and_address_are_still_live(self):
        # Only the three identifiers are pinned. A sheet that froze the name
        # or the premises would be the next thing to contradict FBR.
        markup = render(self.TEMPLATE)
        self.assertIn(PAYLOAD["sellerBusinessName"], markup)
        self.assertIn("Urdu Bazar", markup)

    def test_a_thirteen_digit_buyer_id_is_a_cnic(self):
        data = self.line()
        data["buyerNTNCNIC"] = "3520212345671"
        markup = render(self.TEMPLATE, data)
        cnic = markup.split("CNIC No")[1].split("</tr>")[0]
        self.assertIn("3520212345671", cnic)
        ntn = markup.split("NTN if any")[1].split("</tr>")[0]
        self.assertNotIn("3520212345671", ntn)

    def test_anything_else_is_an_ntn(self):
        # The payload's own buyer, 0178847-7: seven digits and a check digit.
        markup = render(self.TEMPLATE)
        ntn = markup.split("NTN if any")[1].split("</tr>")[0]
        self.assertIn(PAYLOAD["buyerNTNCNIC"], ntn)
        cnic = markup.split("CNIC No")[1].split("</tr>")[0]
        self.assertNotIn(PAYLOAD["buyerNTNCNIC"], cnic)

    def test_the_unit_prints_its_first_term_only(self):
        markup = render(self.TEMPLATE, self.line(uoM="Numbers, pieces, units"))
        self.assertIn(">Numbers<", markup)
        self.assertNotIn("pieces", markup)

    def test_a_single_word_unit_is_untouched(self):
        self.assertIn(">KG<", render(self.TEMPLATE, self.line(uoM="KG")))

    def test_a_two_word_unit_keeps_both_words(self):
        # Taking the first WORD would print "Square", which is a different
        # unit. The rule is the first comma-separated term.
        self.assertIn(">Square Metre<",
                      render(self.TEMPLATE, self.line(uoM="Square Metre")))

    def test_the_column_prints_the_description_alone(self):
        # "Product Code & Description" is their heading, but the code in that
        # column is part of the text they type. The separate Product Code box
        # printed the application's own P-0001 series in front of it, so the
        # client asked for the box to go and the prefix with it.
        markup = render(self.TEMPLATE)
        self.assertIn(">Thermal Paper<", markup)
        self.assertNotIn("-Thermal Paper", markup)

    def test_a_stored_product_code_is_not_printed_either(self):
        # An invoice filed while the box existed still has a productCode on
        # its line. Reprinting it must not bring the prefix back: what that
        # field holds is this application's code, not a part number.
        markup = render(self.TEMPLATE, self.line(productCode="P-0002"))
        self.assertNotIn("P-0002", markup)
        self.assertIn(">Thermal Paper<", markup)

    def test_fbrs_description_of_the_hs_code_prints(self):
        description = "NUCLEAR REACTOR, BOILERS, MACH...NUCL"
        markup = render(self.TEMPLATE, self.line(hsCodeDescription=description))
        self.assertIn(description, markup)

    def test_the_item_type_column_is_the_sale_type(self):
        markup = render(self.TEMPLATE, self.line(saleType="3rd Schedule Goods"))
        self.assertIn("3rd Schedule Goods", markup)

    def test_part_unit_falls_back_to_the_gross_over_the_unit_rate(self):
        # What a line with no figure of its own does -- every line filed
        # before the box existed. The row then multiplies out to the figures
        # printed beside it: 127,779.00 / 669.00 = 191.
        self.assertIn(">191.00<", render(self.TEMPLATE))

    def test_a_typed_part_unit_is_the_one_that_prints(self):
        # Their old software carried it separately and they asked for it
        # back, so a figure they type wins over the derivation.
        # 191 is the derived figure AND the quantity, so the check is that
        # the typed one is in the Part Unit cell, not that 191 is absent.
        markup = render(self.TEMPLATE, self.line(partUnit=26))
        part_unit_cells = markup.count(">26.00<")
        self.assertEqual(2, part_unit_cells,
                         "the line's Part Unit cell and the column total")

    def test_a_typed_zero_prints_as_a_zero(self):
        # Nothing is calculated from this column, so a deliberate zero is a
        # figure like any other and must not be read as "no figure given".
        self.assertIn(">0.00<", render(self.TEMPLATE, self.line(partUnit=0)))

    def test_the_line_multiplies_out_when_it_is_priced_by_part_unit(self):
        # The client's own arithmetic: Part Unit x Unit Rate = Gross Amount,
        # with the quantity a weight that takes no part in it. 26 x 50,000 =
        # 1,300,000 on a line of 30 KG.
        data = self.line(partUnit=26, unitrate=50000, quantity=30,
                         valueSalesExcludingST=1300000,
                         salesTaxApplicable=234000, totalValues=1534000)
        data["totalExcl"] = 1300000
        data["totalTax"] = 234000
        data["totalInclusive"] = 1534000
        markup = render(self.TEMPLATE, data)
        for figure in (">26.00<", ">50,000.00<", ">1,300,000.00<", ">30.00<"):
            self.assertIn(figure, markup, figure)

    def test_the_unit_rate_is_the_one_the_line_carries(self):
        # Not value / quantity, which is what the PDF derives for everyone
        # else and would print 43,333.33 on the line above.
        data = self.line(partUnit=26, unitrate=50000, quantity=30,
                         valueSalesExcludingST=1300000)
        markup = render(self.TEMPLATE, data)
        self.assertIn(">50,000.00<", markup)
        self.assertNotIn("43,333", markup)

    def test_the_total_row_adds_the_figures_that_printed(self):
        # Two lines, one typed and one derived: the column has to add up to
        # what is above it or the sheet contradicts itself.
        data = dict(PAYLOAD, items=[
            dict(PAYLOAD["items"][0], partUnit=26),
            dict(PAYLOAD["items"][0]),
        ])
        # 26 typed + 191 derived = 217.
        self.assertIn(">217.00<", render(self.TEMPLATE, data))

    def test_the_table_is_never_fewer_than_five_lines(self):
        # Their Excel form was a ten-line box that dashed the empty rows.
        # The restyle dropped the padding; the client asked for it back at
        # five, so a short invoice prints its lines and ruled blanks up to
        # five rather than one row floating over white space.
        self.assertEqual(7, len(items_table_rows(render(self.TEMPLATE))),
                         "heading + one line + four blanks + total")
        data = dict(PAYLOAD, items=[PAYLOAD["items"][0]] * 4)
        self.assertEqual(7, len(items_table_rows(render(self.TEMPLATE, data))),
                         "heading + four lines + one blank + total")

    def test_five_is_a_floor_and_not_a_cap(self):
        # An invoice longer than their box runs on and pads nothing.
        data = dict(PAYLOAD, items=[PAYLOAD["items"][0]] * 8)
        self.assertEqual(10, len(items_table_rows(render(self.TEMPLATE, data))),
                         "heading + eight lines + total, no blanks")

    def test_the_blank_rows_carry_no_figures(self):
        # A padding row is a dash in every column. A 0 printed in one would
        # read as a real figure of nought, and the totals would be checked
        # against it.
        markup = render(self.TEMPLATE)
        blanks = [row for row in markup.split('class="pad"')[1:]]
        self.assertEqual(4, len(blanks))
        for row in blanks:
            cells = row.split("</tr>")[0]
            self.assertNotIn("0.00", cells)
            self.assertEqual(13, cells.count("<td"))

    def test_a_deep_line_does_not_change_the_number_of_rows(self):
        # Every cell that can wrap is clipped, so a long description grows
        # its row a little and adds no rows at all.
        deep = "BALANCER UNIT NEW LS 183104 MEL ASSEMBLY COMPLETE SPARE"
        rows = items_table_rows(
            render(self.TEMPLATE, self.line(productDescription=deep)))
        self.assertEqual(7, len(rows), "heading + one line + four blanks + total")

    def test_no_other_replica_grows_these_columns(self):
        data = self.line(hsCodeDescription="NUCLEAR REACTOR, BOILERS",
                         productCode="1183104-1-1")
        for template in set(CLIENT_TEMPLATES) - {self.TEMPLATE}:
            markup = render(template, data)
            self.assertNotIn("HS CODE DESCRIPTION", markup, template)
            self.assertNotIn("1183104-1-1", markup, template)

    def test_no_other_replica_prints_their_pinned_numbers(self):
        for template in set(CLIENT_TEMPLATES) - {self.TEMPLATE}:
            self.assertNotIn(self.PHONE, render(template), template)


class ZahidBrothersFields(unittest.TestCase):
    """Reference, Term of Sales, their product code and the HS description.

    All four are printed-only. The client does not use custom fields -- their
    sheet has named boxes -- so the first two are proper fields on the invoice
    form, and the test that matters most here is the one proving that none of
    the four ever reaches FBR.
    """

    TEMPLATE = "invoice_zahid_brothers.html"
    CLIENT = CLIENT_TEMPLATES["invoice_zahid_brothers.html"]

    # ---- the buyer's registration, printed on the face of the invoice ----

    def test_an_unregistered_buyer_is_stamped_a_retail_consumer(self):
        markup = render(self.TEMPLATE,
                        dict(PAYLOAD, buyerRegistrationType="Unregistered"))
        self.assertIn("Registration Type", markup)
        self.assertIn("Unregistered", markup)
        self.assertIn("Buyer Type", markup)
        self.assertIn("RETAIL CONSUMER", markup)

    def test_a_registered_buyer_gets_nothing_under_its_registration_row(self):
        # The client's instruction: the stamp belongs to unregistered buyers
        # and a registered one is to carry no second row at all.
        markup = render(self.TEMPLATE,
                        dict(PAYLOAD, buyerRegistrationType="Registered"))
        self.assertIn("Registration Type", markup)
        self.assertIn("Registered", markup)
        self.assertNotIn("RETAIL CONSUMER", markup)
        self.assertNotIn("Buyer Type", markup)

    def test_retail_consumer_is_set_bolder_than_the_rest_of_the_card(self):
        # "In bold" was the instruction, and the values on that card are
        # already semi-bold -- so it has to be heavier than they are, not
        # merely bold, or it does not read as a stamp at all.
        markup = render(self.TEMPLATE,
                        dict(PAYLOAD, buyerRegistrationType="Unregistered"))
        self.assertIn('class="val strong">RETAIL CONSUMER', markup)
        self.assertRegex(markup, r"\.ids td\.val\.strong \{[^}]*font-weight: 700")

    def test_an_invoice_with_no_registration_field_claims_nothing(self):
        # Older invoices predate the field. The create form defaults a missing
        # value to "Unregistered", so trusting it here would stamp RETAIL
        # CONSUMER onto a tax document whose buyer may hold an STRN. Printing
        # neither row is the honest answer to a question the record cannot
        # answer -- and is why this reads the raw value rather than a default.
        data = dict(PAYLOAD)
        data.pop("buyerRegistrationType", None)
        markup = render(self.TEMPLATE, data)
        self.assertNotIn("RETAIL CONSUMER", markup)
        self.assertNotIn("Registration Type", markup)

    def test_the_unregistered_spellings_the_rest_of_the_app_accepts(self):
        # compliance/fields.py counts these as registered; the invoice must
        # agree with the report, so the same words are tested here.
        for value in ("registered", "R", "yes", "1"):
            markup = render(self.TEMPLATE,
                            dict(PAYLOAD, buyerRegistrationType=value))
            self.assertNotIn("RETAIL CONSUMER", markup,
                             "%r should count as registered" % value)
        for value in ("Unregistered", "unregistered", "UNREGISTERED"):
            markup = render(self.TEMPLATE,
                            dict(PAYLOAD, buyerRegistrationType=value))
            self.assertIn("RETAIL CONSUMER", markup,
                          "%r should count as unregistered" % value)

    def test_both_rows_print_when_they_are_filled_in(self):
        markup = render(self.TEMPLATE,
                        dict(PAYLOAD, reference="PO-4471",
                             termOfSales="Cash on delivery"))
        self.assertIn("PO-4471", markup)
        self.assertIn("Cash on delivery", markup)

    def test_both_rows_are_ruled_even_when_empty(self):
        # They are boxes on a form, not optional lines: an empty one is a
        # blank rule to write on, which is what their own sheet prints.
        markup = render(self.TEMPLATE)
        self.assertIn("Reference", markup)
        self.assertIn("Term of Sales", markup)

    def test_no_other_replica_prints_either_of_them(self):
        data = dict(PAYLOAD, reference="PO-4471", termOfSales="Cash on delivery")
        for template in set(CLIENT_TEMPLATES) - {self.TEMPLATE}:
            markup = render(template, data)
            self.assertNotIn("PO-4471", markup, template)
            self.assertNotIn("Cash on delivery", markup, template)

    def test_only_this_client_is_offered_the_two_fields(self):
        self.assertTrue(client_features.prints_invoice_reference_fields(self.CLIENT))
        self.assertFalse(client_features.prints_invoice_reference_fields(
            "71a52649-1b77-4748-9a51-fd33ce621000"))
        self.assertFalse(client_features.prints_invoice_reference_fields(None))

    def test_nobody_is_offered_a_product_code_box_any_more(self):
        # This client was the only member of MANUAL_PRODUCT_CODES and asked
        # for the box to go. The switch is kept for the next client who wants
        # a manufacturer's part number, so what is asserted here is that it
        # is EMPTY rather than that it is gone.
        self.assertFalse(client_features.types_own_product_code(self.CLIENT))
        self.assertEqual(frozenset(), client_features.MANUAL_PRODUCT_CODES)

    def test_this_client_types_their_own_part_unit(self):
        self.assertTrue(client_features.types_part_unit(self.CLIENT))
        self.assertFalse(client_features.types_part_unit(
            "ed156fdd-086e-480c-be18-a2c85d9e31b1"))
        self.assertFalse(client_features.types_part_unit(None))

    def test_this_client_opens_fixed_notified_value_at_the_line_value(self):
        self.assertTrue(client_features.fixed_notified_defaults_to_value(self.CLIENT))
        self.assertFalse(client_features.fixed_notified_defaults_to_value(
            "ed156fdd-086e-480c-be18-a2c85d9e31b1"))
        self.assertFalse(client_features.fixed_notified_defaults_to_value(None))

    def test_the_sale_type_survives_for_a_client_who_types_codes(self):
        # The SPECIAL_USERNAMES clients get a code box AND lose the sale type
        # and both SRO fields. This client needs the code box and the sale
        # type both -- Item Type on their sheet IS the sale type -- so the
        # two switches have to stay separate.
        import invoice_form_routes
        self.assertNotIn(self.CLIENT, invoice_form_routes.SPECIAL_USERNAMES)
        source = io.open(os.path.join(ROOT, "invoice_form_routes.py"),
                         encoding="utf-8").read()
        self.assertEqual(
            2,
            source.count("own_product_code = is_special_user or client_features"),
            "create_product and update_product both decide it the same way")

    def test_nothing_here_is_ever_filed(self):
        # The guard that matters. FBR's schemas have none of these four, and
        # an unknown key is what gets an invoice rejected.
        source = io.open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        invoice_fields = source.split("PDF_ONLY_INVOICE_FIELDS = (")[1].split(")")[0]
        for field in ("reference", "termOfSales"):
            self.assertIn('"' + field + '"', invoice_fields, field)
        item_fields = source.split("PDF_ONLY_ITEM_FIELDS = frozenset({")[1].split("})")[0]
        for field in ("hsCodeDescription", "productCode", "partUnit",
                      "unitrate"):
            self.assertIn('"' + field + '"', item_fields, field)

    def test_the_form_is_told_which_clients_get_them(self):
        # The page is rendered with the answer, so the boxes are there on
        # first paint rather than after a username round trip.
        source = io.open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        for name, helper in (
            ("invoice_reference_fields_for_client", "prints_invoice_reference_fields"),
            ("product_code_for_client", "types_own_product_code"),
            ("hs_code_description_for_client", "prints_hs_code_description"),
            ("part_unit_for_client", "types_part_unit"),
        ):
            self.assertIn(name + "=client_features." + helper, source, name)
        form = io.open(os.path.join(ROOT, "templates", "create-invoice.html"),
                       encoding="utf-8").read()
        for name in ("invoice_reference_fields_for_client",
                     "product_code_for_client",
                     "hs_code_description_for_client",
                     "part_unit_for_client",
                     "fixed_notified_from_value_for_client"):
            self.assertIn("{{ 'true' if " + name + " else 'false' }}", form, name)
        for field in ('id="invoice-reference"', 'id="invoice-term-of-sales"',
                      'id="product-hs-description"'):
            self.assertIn(field, form, field)


@unittest.skipIf(WeasyHTML is None, "WeasyPrint/pypdfium2 not installed")
class ZahidBrothersPagination(unittest.TestCase):
    """How many lines come out as one page, measured on a real sheet.

    Their own sheet is 329.9 mm tall. This one is A4, and it has to carry a
    masthead where they had two typed lines and an FBR band they never had,
    so the fit is tight by construction. The filler budget in the template is
    set from these measurements -- re-run them after changing anything above
    or below the items table.

    These used to say ten lines fit. They do not, and never did: the renders
    were handed QR_B64, seventeen bytes that are not a PNG, so the QR came
    out as the words "FBR QR code" and the band measured one text line deep
    instead of the mark's 17mm. Every page here is now rendered with a real
    QR, which is why the figure dropped to nine -- nine is what their
    clients have been getting all along.
    """

    TEMPLATE = "invoice_zahid_brothers.html"
    FOOT = "Name & Designation"
    HEADING = "Value Including Sales Tax"

    @classmethod
    def setUpClass(cls):
        cls.fbr_logo = real_fbr_logo()
        cls.qr = real_qr()

    def markup(self, count, description="PLATE FENDER LH %d"):
        items = [dict(PAYLOAD["items"][0],
                      productDescription=description % (i + 1),
                      productCode="1343051F-3-%d" % (i + 1),
                      hsCodeDescription="VEHICLES OTHER THAN RAILWAY OR...VEHICLE",
                      saleType="3rd Schedule Goods")
                 for i in range(count)]
        # Both FBR marks are real images here. Either one broken collapses
        # the band and hands the items table depth the server never gives it.
        return render(self.TEMPLATE, dict(PAYLOAD, items=items),
                      qr=self.qr, logo=self.fbr_logo)

    def pages(self, count, description="PLATE FENDER LH %d"):
        pdf = WeasyHTML(string=self.markup(count, description),
                        base_url=ROOT).write_pdf()
        document = pypdfium2.PdfDocument(io.BytesIO(pdf))
        return [document[i].get_textpage().get_text_bounded()
                for i in range(len(document))]

    def test_a_one_line_invoice_is_one_page(self):
        self.assertEqual(1, len(self.pages(1)))

    def test_their_own_five_line_invoice_is_one_page(self):
        self.assertEqual(1, len(self.pages(5)))

    def test_their_nine_line_form_is_one_page(self):
        self.assertEqual(1, len(self.pages(9)))

    def test_the_ninth_line_is_the_last_one_that_fits(self):
        # Measured on the restyled sheet with both FBR marks rendering:
        # nine lines and the whole closing block fit one page, the tenth
        # starts a second. A change anywhere outside the table shows up
        # here first rather than on a client's invoice.
        self.assertEqual(1, len(self.pages(9)))
        self.assertEqual(2, len(self.pages(10)))

    def test_no_money_column_is_too_narrow_for_its_figures(self):
        # A wrapped figure reads as two figures -- "1,534,000.0" over "0" --
        # and that is exactly what the first cut of the restyle printed in
        # the Value Including Sales Tax column. This measures the rendered
        # line boxes against the cells that hold them, so a column narrowed
        # for any reason fails here rather than on an invoice.
        # Five of their own lines, so the TOTAL row is measured too -- the
        # Qty column was wide enough for a line's 64.00 and not for the
        # column's 320.00, which is the shape of mistake this is here for.
        line = dict(PAYLOAD["items"][0], quantity=64, partUnit=8,
                    unitrate=8847.97, valueSalesExcludingST=70783.76,
                    salesTaxApplicable=12741.08, totalValues=83524.84)
        markup = render(self.TEMPLATE, dict(
            PAYLOAD, items=[line] * 5,
            totalExcl=353918.80, totalTax=63705.40, totalInclusive=417624.20))
        page = WeasyHTML(string=markup, base_url=ROOT).render().pages[0]

        def walk(box):
            yield box
            for child in getattr(box, "children", ()) or ():
                for item in walk(child):
                    yield item

        px_mm = 25.4 / 96.0
        wrapped = []
        for box in walk(page._page_box):
            if getattr(box, "element_tag", None) != "td":
                continue
            try:
                classes = (box.element.get("class") or "").split()
            except Exception:
                classes = []
            if "num" not in classes:
                continue
            lines = [inner for inner in walk(box)
                     if type(inner).__name__ == "LineBox"]
            # Comparing the widest line against the cell cannot find this:
            # wrapping is how the text AVOIDS being wider than its box, so
            # both halves measure narrow and the cell looks fine. A numeric
            # cell holds one figure, so the tell is the line COUNT.
            if len(lines) > 1:
                text = "".join(
                    child.text for inner in lines for child in walk(inner)
                    if type(child).__name__ == "TextBox")
                wrapped.append("%r over %d lines in a %.2fmm cell"
                               % (text, len(lines), box.width * px_mm))
        self.assertEqual([], wrapped)

    def test_the_items_heading_stays_three_lines_deep(self):
        # The heading band is tracked out so a row of small capitals reads
        # as a heading. Tracking is paid for in width, and "Further Sales
        # Tax if ANY" is the heading that sets its own column: past about
        # 0.24pt it breaks to a fourth line, the band grows from 6.45mm to
        # 8.60mm, and every page loses that depth from its item rows. So
        # the band's depth is the thing to hold, not the tracking.
        markup = render(self.TEMPLATE, PAYLOAD)
        page = WeasyHTML(string=markup, base_url=ROOT).render().pages[0]

        def walk(box):
            yield box
            for child in getattr(box, "children", ()) or ():
                for item in walk(child):
                    yield item

        deepest = []
        for box in walk(page._page_box):
            if getattr(box, "element_tag", None) != "th":
                continue
            lines = [inner for inner in walk(box)
                     if type(inner).__name__ == "LineBox"]
            if len(lines) > 3:
                text = "".join(
                    child.text for inner in lines for child in walk(inner)
                    if type(child).__name__ == "TextBox")
                deepest.append("%r over %d lines" % (text, len(lines)))
        self.assertEqual([], deepest)

    # The face the Linux server falls back to. Measuring in it is the only
    # way this suite sees what the client is actually sent; everything else
    # here renders in whatever this machine happens to have.
    SERVER_FONT = '"DejaVu Sans", sans-serif'

    def _render_in_server_font(self, data):
        markup = render(self.TEMPLATE, data)
        markup, n = re.subn(r"(body \{\s*\n\s*font-family: )[^;]+;",
                            lambda m: m.group(1) + self.SERVER_FONT + ";",
                            markup, count=1, flags=re.S)
        self.assertEqual(1, n, "could not substitute the body font")
        return markup

    def test_nothing_wraps_in_the_font_the_server_actually_has(self):
        """The client's HS code printed over two lines; here it never did.

        The page asks for Segoe UI and falls back through Helvetica and Arial
        to DejaVu Sans. A Windows desktop resolves the first, the Linux box
        that prints these invoices resolves the last, and the last is
        appreciably wider. Sized against Segoe UI alone, three cells fitted on
        every render here and wrapped on the client's own sheet: the HS code
        ("2710.195" over "1"), the Sales Tax total ("12,741.0" over "8") and
        the "Sr.No." heading ("Sr.N" over "o.").

        A figure split across two lines reads as two figures, and a split HS
        code reads as two codes, so this checks the money columns, the HS code
        and every heading that is a single word -- a heading of several words
        may wrap between them, which is how the band is designed.
        """
        line = dict(PAYLOAD["items"][0], hsCode="2710.1951", quantity=64,
                    partUnit=8, unitrate=8847.97,
                    valueSalesExcludingST=70783.76,
                    salesTaxApplicable=12741.08, totalValues=83524.84)
        markup = self._render_in_server_font(dict(
            PAYLOAD, items=[line] * 5,
            totalExcl=353918.80, totalTax=63705.40, totalInclusive=417624.20))
        page = WeasyHTML(string=markup, base_url=ROOT).render().pages[0]

        def walk(box):
            yield box
            for child in getattr(box, "children", ()) or ():
                for item in walk(child):
                    yield item

        px_mm = 25.4 / 96.0
        wrapped = []
        for box in walk(page._page_box):
            tag = getattr(box, "element_tag", None)
            if tag not in ("td", "th"):
                continue
            try:
                classes = (box.element.get("class") or "").split()
            except Exception:
                classes = []
            lines = [b for b in walk(box) if type(b).__name__ == "LineBox"]
            if len(lines) < 2:
                continue
            text = "".join(c.text for ln in lines for c in walk(ln)
                           if type(c).__name__ == "TextBox")
            if tag == "th":
                # Several words may share a heading's lines; one word may not.
                # The heading's own source text is what says which it is --
                # the rendered line boxes come back with the spaces at the
                # breaks already consumed, so "Part Unit" reads as one token
                # there and would be mistaken for a split word.
                source = (getattr(box.element, "text", "") or "").strip()
                if " " in source:
                    continue
            elif not ({"hs", "num"} & set(classes)):
                continue
            wrapped.append("%r over %d lines in a %.2fmm cell"
                           % (text, len(lines), box.width * px_mm))

        # A cell that cannot wrap does not wrap when it is too narrow -- it
        # OVERFLOWS, and .items sets overflow:hidden, so the surplus is
        # simply cut off. On the HS code that means a truncated code printed
        # as though it were the whole one, which is worse than the split the
        # nowrap was added to prevent. Counting lines cannot see it; this
        # measures the text against the box it had to fit in.
        clipped = []
        for box in walk(page._page_box):
            if getattr(box, "element_tag", None) not in ("td", "th"):
                continue
            try:
                classes = (box.element.get("class") or "").split()
            except Exception:
                classes = []
            if "hs" not in classes:
                continue
            for inner in walk(box):
                if type(inner).__name__ != "LineBox":
                    continue
                if inner.width > box.width + 0.5:
                    text = "".join(c.text for c in walk(inner)
                                   if type(c).__name__ == "TextBox")
                    clipped.append(
                        "%r needs %.2fmm and was given %.2fmm"
                        % (text, inner.width * px_mm, box.width * px_mm))

        self.assertEqual([], wrapped + clipped)

    def test_the_closing_block_never_lands_alone_on_a_second_page(self):
        # The failure this exists for: a last page holding nothing but the
        # signature rules and the FBR band reads as a second, empty invoice.
        for count in (1, 5, 10, 13, 20, 30):
            pages = self.pages(count)
            self.assertIn(self.FOOT, pages[-1], "%d items" % count)
            if len(pages) > 1:
                self.assertIn(
                    self.HEADING, pages[-1],
                    "%d items: the closing page has no items table above "
                    "the foot" % count)

    def test_a_line_past_capacity_spills_rather_than_being_dropped(self):
        text = "".join(self.pages(30))
        self.assertIn("PLATE FENDER LH 30", re.sub(r"\s+", " ", text))


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

    # The cases above that are figures, and so may be re-spelled by a
    # replica's own notation (see FIGURE_NOTATION). A tax number or an HS
    # code is an identifier, not a figure, and is never re-spelled -- a
    # template that printed one of those differently would be printing a
    # different identifier.
    FIGURES = {"quantity", "unit price", "value excluding tax", "sales tax",
               "value including tax"}

    # Zahid Brothers asked for their phone, NTN and sales tax registration
    # number to print the same whatever the business profile says, so those
    # two identifiers are fixed text in their template rather than payload
    # fields. That is NOT a waiver: the sheet still has to show a seller NTN
    # and a seller STRN, and they still have to be THEIR registration -- so
    # the check is applied against the pinned value instead. The risk the
    # client accepted is that a change of registration has to be made in the
    # template; the risk this table refuses is the numbers disappearing.
    FIXED = {
        "invoice_zahid_brothers.html": {
            "seller NTN": "1472236-4",
            "seller STRN": "3520267684365",
        },
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
            respell = FIGURE_NOTATION.get(template, {})
            pinned = self.FIXED.get(template, {})
            for label, value in self.CASES.items():
                if label in waived:
                    continue
                if label in pinned:
                    value = pinned[label]
                elif label in respell:
                    value = respell[label](value)
                self.assertIn(value, markup, f"{template}: {label} missing")

    def test_a_pinned_identifier_names_a_real_template_and_a_real_field(self):
        # Pinning is a decision the client made about two identifiers. This
        # fails if an entry is left behind for a template that no longer
        # exists, names a field that is not checked, or is quietly widened
        # to a figure -- a pinned figure would stop the sheet being checked
        # against the payload at all, which is the opposite of the point.
        for template, pins in self.FIXED.items():
            self.assertIn(template, CLIENT_TEMPLATES, template)
            for label, value in pins.items():
                self.assertIn(label, self.CASES, label)
                self.assertNotIn(
                    label, self.FIGURES,
                    f"{template}: {label} is a figure, and a figure is never pinned")
                self.assertTrue(value.strip(), f"{template}: {label} pinned to nothing")

    def test_a_pinned_identifier_is_not_also_waived(self):
        for template, pins in self.FIXED.items():
            self.assertFalse(
                set(pins) & self.WAIVED.get(template, set()),
                f"{template}: an identifier cannot be both pinned and waived")

    def test_nothing_is_waived_that_the_client_did_not_ask_to_waive(self):
        # A waiver is a decision, not a way around a failing assertion. This
        # fails if one is left behind for a template that no longer exists or
        # names a field that is not checked.
        for template, labels in self.WAIVED.items():
            self.assertIn(template, CLIENT_TEMPLATES, template)
            for label in labels:
                self.assertIn(label, self.CASES, label)

    def test_a_notation_names_a_real_template_and_changes_no_figure(self):
        # A re-spelling is not a waiver: it keeps the figure under test and
        # only changes how it is written. This fails if an entry is left
        # behind for a template that no longer exists, if it is applied to
        # an identifier rather than a figure, or if one is ever written so
        # loosely that it would alter the number being checked.
        #
        # The rule: the re-spelled digits must be the original's digits, or
        # the original's with trailing zeros dropped -- which is what
        # writing '191.00' as '191' does. Nothing may be invented, and no
        # digit may change.
        for template, respellings in FIGURE_NOTATION.items():
            self.assertIn(template, CLIENT_TEMPLATES, template)
            for label, respell in respellings.items():
                self.assertIn(label, self.CASES, label)
                self.assertIn(
                    label, self.FIGURES,
                    f"{template}: {label} is an identifier, not a figure")
                original = re.sub(r"\D", "", self.CASES[label])
                respelled = re.sub(r"\D", "", respell(self.CASES[label]))
                self.assertTrue(
                    original == respelled
                    or (original.startswith(respelled)
                        and set(original[len(respelled):]) <= {"0"}),
                    f"{template}: the {label} notation changed the digits "
                    f"({original} -> {respelled})")

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
        "invoice_rubberx.html": "brand/rubberx.html",
        "invoice_zahid_brothers.html": "brand/zahid_brothers.html",
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
