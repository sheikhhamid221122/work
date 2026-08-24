"""
Tests for the per-client compliance layer.

Run:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from compliance import (  # noqa: E402
    apply_compliance,
    get_profile,
    merge_template_settings,
    resolve_template_name,
    run_validation,
)


def sample_invoice():
    return {
        "sellerBusinessName": "FINE PRINTERS",
        "sellerAddress": "SHOP NO.2. MANMOHAN SINGH BUILDING, MAIN CHOWK, ROYAL PARK, LAHORE",
        "sellerNTNCNIC": "3356156",
        "sellerSTRN": "03-00-8442-049-37",
        "fbrInvoiceNumber": "3520195158363DIX40LCQ670074",
        "buyerBusinessName": "Point print pk private",
        "buyerAddress": "D-67, Block 4. Clifton Karachi, Saddar Town",
        "buyerProvince": "Sindh",
        "buyerNTNCNIC": "4200892",
        "buyerSTRN": "1700420089212",
        "buyerRegistrationType": "Registered",
        "invoiceDate": "2026-08-15",
        "invoiceRefNo": "1634",
        "PO": "276492",
        "items": [{"productDescription": "Trade Letter Double sided", "hs_code": "4901.1000",
                   "quantity": 30000, "rate": "18%", "unitrate": 2.74,
                   "valueSalesExcludingST": 82200.0, "salesTaxApplicable": 14796.0}],
        "totalExcl": 82200.0, "totalTax": 14796.0, "totalInclusive": 96996.0,
    }


class OtherClientsUnaffected(unittest.TestCase):
    """The whole point: nothing changes for anyone without a profile."""

    def test_unknown_username_has_no_profile(self):
        for username in ("8974121", "5207949", "3520204956465", "", None):
            self.assertIsNone(get_profile(username))

    def test_apply_compliance_is_a_noop_without_profile(self):
        data = sample_invoice()
        before = dict(data)
        self.assertEqual(apply_compliance(data, {"show_po": True}, None), before)
        self.assertNotIn("infoRows", data)
        self.assertNotIn("invoiceDateDisplay", data)
        self.assertEqual(data["sellerSTRN"], "03-00-8442-049-37")

    def test_validation_is_a_noop_without_profile(self):
        self.assertEqual(run_validation(sample_invoice(), None), [])

    def test_template_choice_untouched_without_profile(self):
        self.assertEqual(
            resolve_template_name("8974121", "invoice_template3.html"),
            "invoice_template3.html",
        )

    def test_merge_does_not_mutate_caller_settings(self):
        original = {"show_po": False, "max_item_rows": 6}
        merged = merge_template_settings(original, get_profile("Fineprinters"))
        self.assertEqual(original, {"show_po": False, "max_item_rows": 6})
        self.assertTrue(merged["show_po"])


class FinePrintersProfile(unittest.TestCase):
    def setUp(self):
        self.profile = get_profile("Fineprinters")
        self.assertIsNotNone(self.profile)
        self.settings = merge_template_settings({}, self.profile)
        self.data = apply_compliance(sample_invoice(), self.settings, self.profile)

    def test_username_match_is_case_insensitive(self):
        self.assertIsNotNone(get_profile("fineprinters"))
        self.assertIsNotNone(get_profile(" FinePrinters "))

    def test_approved_template_flags(self):
        self.assertFalse(self.settings["show_seller_legal_name"])
        self.assertFalse(self.settings["show_signature_block"])
        self.assertEqual(self.settings["seller_address_label"], "Seller Address:")
        self.assertEqual(self.settings["max_item_rows"], 5)

    def test_seller_strn_normalised_ntn_untouched(self):
        self.assertEqual(self.data["sellerSTRN"], "0300844204937")
        self.assertEqual(self.data["sellerNTNCNIC"], "3356156")

    def test_buyer_canonical_values(self):
        self.assertEqual(self.data["buyerBusinessName"], "Point Print PK (Pvt.) Ltd")
        self.assertEqual(self.data["buyerAddress"],
                         "D-67/1, Block 4, Clifton, Karachi, Sindh, Pakistan")
        self.assertEqual(self.data["buyerNTNCNIC"], "4200892-1")   # check digit kept

    def test_info_rows_order_and_labels(self):
        rows = [(r["label"], r["value"]) for r in self.data["infoRows"]]
        self.assertEqual(rows, [
            ("BUYER NTN/CNIC", "4200892-1"),
            ("BUYER STRN", "1700420089212"),
            ("STATUS", "Registered"),
            ("P.O #", "276492"),
            ("CURRENCY", "PKR"),
            ("TIME OF ISSUE", self.data["issueTime"]),
        ])

    def test_date_display_format(self):
        self.assertEqual(self.data["invoiceDateDisplay"], "15-08-2026")
        self.assertEqual(self.data["invoiceDate"], "2026-08-15")   # payload untouched

    def test_time_of_issue_filled_in_client_timezone(self):
        self.assertRegex(self.data["issueTime"], r"^\d{2}:\d{2} (AM|PM)$")

    def test_no_findings_for_a_good_invoice(self):
        self.assertEqual(run_validation(self.data, self.profile, log=False), [])

    def test_missing_po_is_reported(self):
        data = sample_invoice()
        data.pop("PO")
        data = apply_compliance(data, self.settings, self.profile)
        fields = [f["field"] for f in run_validation(data, self.profile, log=False)]
        self.assertIn("po", fields)

    def test_required_row_renders_even_when_empty(self):
        data = sample_invoice()
        data.pop("PO")
        data = apply_compliance(data, self.settings, self.profile)
        self.assertIn("P.O #", [r["label"] for r in data["infoRows"]])

    def test_other_buyers_of_this_client_get_base_rows_only(self):
        data = sample_invoice()
        data.update({"buyerBusinessName": "Some Other Buyer",
                     "buyerNTNCNIC": "1234567", "buyerSTRN": "9876543210123"})
        data.pop("PO")
        data = apply_compliance(data, self.settings, self.profile)
        labels = [r["label"] for r in data["infoRows"]]
        self.assertNotIn("P.O #", labels)                       # not required for them
        self.assertEqual(data["buyerBusinessName"], "Some Other Buyer")
        self.assertIn("CURRENCY", labels)

    def test_excel_route_uses_approved_template(self):
        self.assertEqual(
            resolve_template_name("Fineprinters", "invoice_template3.html"),
            "invoice_template_universal.html",
        )

    def test_profile_is_environment_agnostic(self):
        """Same profile for sandbox and production — matching is on username."""
        for env in ("sandbox", "production"):
            data = sample_invoice()
            data["env"] = env
            data = apply_compliance(data, self.settings, self.profile)
            self.assertEqual(data["buyerNTNCNIC"], "4200892-1")


if __name__ == "__main__":
    unittest.main()
