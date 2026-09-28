"""
Tests for master_data.py: the duplicate and validation rules shared by the
Buyers / Products pages, their Excel import, and the invoice form's endpoints.

No database: the rules work on rows already loaded, which is what is tested.
Code numbering and the SQL around it were exercised against a real Postgres
when this was written; they need a database and are not repeated here.

Run:  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import master_data as md  # noqa: E402

BUYERS = [
    # (id, business_name, ntn_cnic, buyer_code)
    (1, "City Care Hospitals", "4231987", "B-0007"),
    (2, "Shifa Medical", "3520212345671", "B-0008"),
]

PRODUCTS = [
    # (id, description, rate, is_active)
    (1, "Paracetamol 500mg", Decimal("320.00"), True),
    (2, "Old Item", Decimal("100.00"), False),
]


class BuyerDuplicates(unittest.TestCase):
    def test_same_ntn_is_a_duplicate_whatever_the_name(self):
        clash = md.find_duplicate_buyer(BUYERS, "Totally Different", "42-31-987")
        self.assertEqual(clash[0], "ntn")
        self.assertEqual(clash[1][0], 1)

    def test_cnic_with_dashes_matches(self):
        self.assertEqual(md.find_duplicate_buyer(BUYERS, "X", "35202-1234567-1")[0], "ntn")

    def test_same_name_ignoring_case_and_spacing_is_a_duplicate(self):
        clash = md.find_duplicate_buyer(BUYERS, "  city   CARE hospitals ", "9999999")
        self.assertEqual(clash[0], "name")

    def test_new_buyer_is_not_a_duplicate(self):
        self.assertIsNone(md.find_duplicate_buyer(BUYERS, "New Co", "9999999"))

    def test_editing_a_buyer_does_not_clash_with_itself(self):
        self.assertIsNone(md.find_duplicate_buyer(BUYERS, "City Care Hospitals", "4231987", exclude_id=1))

    def test_message_names_the_existing_buyer_and_code(self):
        message = md.buyer_duplicate_message(md.find_duplicate_buyer(BUYERS, "X", "4231987"))
        self.assertIn("City Care Hospitals", message)
        self.assertIn("B-0007", message)

    def test_code_owner_is_case_insensitive(self):
        self.assertEqual(md.find_buyer_code_owner(BUYERS, "b-0007")[0], 1)
        self.assertIsNone(md.find_buyer_code_owner(BUYERS, "B-0007", exclude_id=1))


class ProductDuplicates(unittest.TestCase):
    def test_same_name_and_rate_is_a_duplicate(self):
        self.assertEqual(md.find_duplicate_product(PRODUCTS, "paracetamol  500MG", "320")[0], 1)

    def test_same_name_different_rate_is_allowed(self):
        self.assertIsNone(md.find_duplicate_product(PRODUCTS, "Paracetamol 500mg", "350"))

    def test_rate_compared_to_the_paisa(self):
        self.assertIsNotNone(md.find_duplicate_product(PRODUCTS, "Paracetamol 500mg", "320.001"))
        self.assertIsNone(md.find_duplicate_product(PRODUCTS, "Paracetamol 500mg", "320.01"))

    def test_deleted_products_are_found_separately_for_restoring(self):
        self.assertIsNone(md.find_duplicate_product(PRODUCTS, "Old Item", "100"))
        self.assertEqual(md.find_duplicate_product(PRODUCTS, "Old Item", "100", active=False)[0], 2)


class Codes(unittest.TestCase):
    def test_format_pads_to_four_and_grows_past_it(self):
        self.assertEqual(md.format_code("B", 1), "B-0001")
        self.assertEqual(md.format_code("P", 12345), "P-12345")

    def test_reserving_codes_works_with_a_uuid_client_id(self):
        # clients.id is a UUID on the live database. pg_advisory_xact_lock
        # takes an int4, so passing the id itself failed with "invalid input
        # syntax for type integer" and no buyer or product could be added.
        class Cursor:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=None):
                self.calls.append((sql, params))

            def fetchone(self):
                return (7,)

        cur = Cursor()
        client_id = "ddc5c02b-02e0-45fc-92f8-a202c022ae79"
        codes = md.reserve_codes(cur, "buyers", "buyer_code", "B", client_id, count=2)
        self.assertEqual(codes, ["B-0008", "B-0009"])
        lock_sql, lock_params = cur.calls[0]
        self.assertIn("hashtext(%s)", lock_sql)
        self.assertEqual(lock_params[1], client_id)
        self.assertEqual(cur.calls[1][1][-1], client_id)  # codes still filtered by the real id


class Validation(unittest.TestCase):
    PROVINCES = {"SINDH": "SINDH", "PUNJAB": "PUNJAB"}

    def province(self, value):
        return self.PROVINCES.get(str(value or "").strip().upper(), "")

    def buyer(self, **overrides):
        data = {"business_name": "Acme", "ntn_cnic": "1234567", "province": "sindh",
                "registration_type": "registered", "address": "Karachi"}
        data.update(overrides)
        return md.clean_buyer(data, self.province)

    def test_valid_buyer_is_normalised(self):
        clean, error = self.buyer(business_name="  Acme   Traders ")
        self.assertIsNone(error)
        self.assertEqual(clean["business_name"], "Acme Traders")
        self.assertEqual(clean["province"], "SINDH")
        self.assertEqual(clean["registration_type"], "Registered")

    def test_buyer_required_fields(self):
        for field in ("business_name", "province", "registration_type", "address"):
            clean, error = self.buyer(**{field: ""})
            self.assertIsNone(clean, field)
            self.assertTrue(error)

    def test_buyer_tax_id_shape(self):
        self.assertIsNone(self.buyer(ntn_cnic="123")[0])
        self.assertIsNotNone(self.buyer(ntn_cnic="3520212345671")[0])

    def product(self, **overrides):
        data = {"description": "Oil", "uom": "can", "default_tax_rate": "18%", "rate": "1,450.50"}
        data.update(overrides)
        return md.clean_product(data, lambda v: {"can": "CAN"}.get(str(v or "").lower(), ""))

    def test_valid_product_is_normalised(self):
        clean, error = self.product()
        self.assertIsNone(error)
        self.assertEqual(clean["uom"], "CAN")
        self.assertEqual(clean["rate"], Decimal("1450.50"))
        self.assertEqual(clean["default_tax_rate"], Decimal("18.00"))

    def test_product_rejects_bad_values(self):
        self.assertIsNone(self.product(uom="parsec")[0])
        self.assertIsNone(self.product(default_tax_rate="")[0])
        self.assertIsNone(self.product(default_tax_rate="101")[0])
        self.assertIsNone(self.product(rate="-1")[0])
        self.assertIsNone(self.product(hs_code="12")[0])
        self.assertIsNotNone(self.product(hs_code="3004.9099")[0])


if __name__ == "__main__":
    unittest.main()
