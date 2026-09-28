"""
The Download Center must print an invoice with the client's assigned template,
and with the client's logo and the FBR logo.

Its PDFs (single download, bulk ZIP, and the copy stored at submission) are
built by generate_invoice_pdf_for_client, a different function from the one
behind the Create Invoice download. When per-client templates were added only
the Create Invoice path read clients.tpl_template, so every Download Center PDF
silently fell back to the universal template -- the client's old look.

No database is touched: get_db_connection is replaced with a fake that answers
the three queries the function makes, and rendering is intercepted so the test
sees which template was chosen.

Run:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock
from urllib.parse import urlparse
from urllib.request import url2pathname

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402

INVOICE = {
    "invoiceRefNo": "INV-1", "invoiceDate": "2026-09-01",
    "fbrInvoiceNumber": "1234567DI0001",
    "sellerBusinessName": "Seller", "sellerNTNCNIC": "1234567",
    "buyerBusinessName": "Buyer", "buyerNTNCNIC": "7654321",
    "items": [{"productDescription": "Paper", "quantity": 2,
               "valueSalesExcludingST": 1000.0, "salesTaxApplicable": 180.0,
               "totalValues": 1180.0, "rate": "18%"}],
}


def _fake_connection(tpl_template=None, accent=None, user_logo=None, client_logo=None, fbr_logo=None):
    # Column 1 is clients.logo_url and the first 24 columns are the older
    # tpl_show_* settings; None takes their defaults. The template-choice
    # columns follow in the order selected.
    client_row = (None, client_logo) + (None,) * 22 + (tpl_template, accent) + (None,) * 8

    cur = mock.MagicMock()

    def execute(sql, params=None):
        if "FROM clients WHERE id" in sql:
            cur.fetchone.return_value = client_row
        elif "FROM users u JOIN clients" in sql:
            cur.fetchone.return_value = ("1234567", user_logo)
        else:  # fbr logo
            cur.fetchone.return_value = (fbr_logo,)

    cur.execute.side_effect = execute
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    return conn


class DownloadUsesAssignedTemplate(unittest.TestCase):

    def render(self, tpl_template=None, accent=None, with_context=False, **logos):
        rendered = []

        def fake_render(name, **context):
            rendered.append((name, context))
            return "<html></html>"

        pdf = mock.MagicMock()
        pdf.return_value.write_pdf.side_effect = lambda stream: stream.write(b"%PDF-1.7")
        conn = _fake_connection(tpl_template, accent, **logos)
        with mock.patch.object(appmod, "get_db_connection", return_value=conn), \
                mock.patch.object(appmod, "render_template", side_effect=fake_render), \
                mock.patch.object(appmod, "HTML", pdf):
            out = appmod.generate_invoice_pdf_for_client(dict(INVOICE), 1)
        self.assertEqual(out, b"%PDF-1.7")
        self.assertEqual(len(rendered), 1)
        name, context = rendered[0]
        return (name, context) if with_context else (name, context.get("settings") or {})

    def test_hand_built_replica_is_used(self):
        name, _ = self.render("invoice_paper_experts.html")
        self.assertEqual(name, "invoice_paper_experts.html")

    def test_chosen_layout_and_its_colour_are_used(self):
        name, settings = self.render("classic", "#0f766e")
        self.assertEqual(name, "invoices/layouts/classic.html")
        self.assertEqual(settings.get("accent_color"), "#0f766e")

    def test_client_without_a_choice_keeps_the_universal_template(self):
        name, _ = self.render(None)
        self.assertEqual(name, "invoice_template_universal.html")

    def test_unknown_value_falls_back_rather_than_failing(self):
        name, _ = self.render("invoice_that_was_deleted.html")
        self.assertEqual(name, "invoice_template_universal.html")


# A logo that ships in the repo, with the space in its name that the real
# uploads have.
SHIPPED_LOGO = "/static/uploads/FBR LOGO.png"


class DownloadPrintsLogos(unittest.TestCase):
    """Logos are stored site-relative. WeasyPrint gets no base_url, so a path
    passed through unchanged printed the alt text "Logo" / "FBR Logo"."""

    render = DownloadUsesAssignedTemplate.render

    def assertLoadable(self, url):
        self.assertTrue(url and url.startswith("file:"), url)
        local = url2pathname(urlparse(url).path)
        self.assertTrue(os.path.isfile(local), local)

    def test_both_logos_reach_the_template_as_loadable_files(self):
        _, context = self.render(user_logo=SHIPPED_LOGO, fbr_logo=SHIPPED_LOGO, with_context=True)
        self.assertLoadable(context["client_logo_url"])
        self.assertLoadable(context["fbr_logo_url"])

    def test_clients_logo_url_is_used_when_the_user_has_no_logo(self):
        _, context = self.render(client_logo=SHIPPED_LOGO, with_context=True)
        self.assertLoadable(context["client_logo_url"])

    def test_no_logo_stays_empty_so_templates_keep_their_fallback(self):
        _, context = self.render(with_context=True)
        self.assertIsNone(context["client_logo_url"])
        self.assertIsNone(context["fbr_logo_url"])


class PdfAssetUrl(unittest.TestCase):

    def test_shipped_static_file_becomes_a_file_uri(self):
        for stored in (SHIPPED_LOGO, SHIPPED_LOGO.lstrip("/"), SHIPPED_LOGO + "?v=3"):
            url = appmod._pdf_asset_url(stored)
            self.assertTrue(url.startswith("file:"), (stored, url))
            self.assertTrue(os.path.isfile(url2pathname(urlparse(url).path)), stored)

    def test_absolute_and_inline_sources_are_left_alone(self):
        for stored in ("https://cdn.example.com/logo.png", "http://x/y.png", "data:image/png;base64,AAAA"):
            self.assertEqual(appmod._pdf_asset_url(stored), stored)

    def test_empty_is_none(self):
        for stored in (None, "", "   "):
            self.assertIsNone(appmod._pdf_asset_url(stored))

    def test_missing_file_is_prefixed_with_the_site_address(self):
        with mock.patch.dict(os.environ, {"BASE_URL": "https://taxlinkpro.cloud/"}):
            self.assertEqual(appmod._pdf_asset_url("/static/uploads/not-there.png"),
                             "https://taxlinkpro.cloud/static/uploads/not-there.png")
        with mock.patch.dict(os.environ, {"BASE_URL": ""}), \
                appmod.app.test_request_context("/", base_url="https://erp.example.com/"):
            self.assertEqual(appmod._pdf_asset_url("/static/uploads/not-there.png"),
                             "https://erp.example.com/static/uploads/not-there.png")

    def test_a_path_cannot_climb_out_of_static(self):
        url = appmod._pdf_asset_url("/static/../app.py")
        self.assertFalse(str(url).startswith("file:"), url)


if __name__ == "__main__":
    unittest.main()
