"""
Tests for the invoice template designer's placement spec.

The guarantees under test are the ones that keep a user-designed template
printable and FBR-compliant: the QR block cannot be removed or shrunk off the
page, the items table always exists, geometry stays on the paper, and the
items-table columns resolve to something that fits.

Run:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import invoice_spec  # noqa: E402
from compliance.fields import meta_rows, resolve_field  # noqa: E402


class ComplianceGuarantees(unittest.TestCase):
    """The parts a user must not be able to design away."""

    def test_empty_spec_still_carries_fbr_and_items(self):
        types = [e["type"] for e in invoice_spec.validate({})["elements"]]
        self.assertIn("fbr", types)
        self.assertIn("items", types)

    def test_fbr_block_cannot_be_deleted(self):
        spec = invoice_spec.validate({"elements": [{"type": "text", "text": "hi"}]})
        self.assertEqual(1, sum(1 for e in spec["elements"] if e["type"] == "fbr"))

    def test_fbr_block_cannot_be_shrunk_below_scannable_size(self):
        spec = invoice_spec.validate(
            {"elements": [{"type": "fbr", "w": 1, "h": 1, "x": 0, "y": 0}]})
        fbr = [e for e in spec["elements"] if e["type"] == "fbr"][0]
        self.assertGreaterEqual(fbr["w"], invoice_spec.FBR_MIN_MM)
        self.assertGreaterEqual(fbr["h"], invoice_spec.FBR_MIN_MM)

    def test_duplicate_singletons_are_collapsed(self):
        spec = invoice_spec.validate({"elements": [
            {"type": "fbr"}, {"type": "fbr"},
            {"type": "items"}, {"type": "items"},
        ]})
        types = [e["type"] for e in spec["elements"]]
        self.assertEqual(1, types.count("fbr"))
        self.assertEqual(1, types.count("items"))

    def test_elements_are_clamped_onto_the_page(self):
        spec = invoice_spec.validate({"elements": [
            {"type": "text", "x": -500, "y": 9999, "w": 9999, "h": 9999},
        ]})
        for e in spec["elements"]:
            self.assertGreaterEqual(e["x"], 0)
            self.assertGreaterEqual(e["y"], 0)
            self.assertLessEqual(e["x"] + e["w"], invoice_spec.CANVAS_W_MM + 0.01)
            self.assertLessEqual(e["y"] + e["h"], invoice_spec.CANVAS_H_MM + 0.01)

    def test_unknown_types_and_junk_are_dropped(self):
        spec = invoice_spec.validate({"elements": [
            {"type": "definitely-not-a-type"}, "not a dict", None, 42,
        ]})
        self.assertEqual({"fbr", "items"},
                         {e["type"] for e in spec["elements"]})

    def test_hostile_strings_are_bounded_and_colours_validated(self):
        spec = invoice_spec.validate({"elements": [
            {"type": "text", "text": "x" * 99999, "color": "javascript:alert(1)"},
        ]})
        text = [e for e in spec["elements"] if e["type"] == "text"][0]
        self.assertEqual(invoice_spec.MAX_TEXT, len(text["text"]))
        self.assertEqual("", text["color"])

    def test_validate_is_idempotent(self):
        once = invoice_spec.validate(invoice_spec.default_spec())
        self.assertEqual(once, invoice_spec.validate(once))


class ItemColumns(unittest.TestCase):
    """Columns a designer template may print, per the FBR DI API catalogue."""

    def test_description_is_always_present(self):
        cols = [c["key"] for c in invoice_spec.item_columns(["serial", "total"])]
        self.assertIn("description", cols)

    def test_columns_render_in_catalogue_order_not_click_order(self):
        cols = [c["key"] for c in
                invoice_spec.item_columns(["total", "serial", "sales_tax"])]
        self.assertEqual(["serial", "description", "sales_tax", "total"], cols)

    def test_widths_leave_room_for_the_description(self):
        cols = invoice_spec.item_columns(list(invoice_spec.ITEM_COLUMNS))
        fixed = sum(c["width"] or 0 for c in cols)
        self.assertLessEqual(fixed, 100.0 - invoice_spec.MIN_DESC_PCT + 0.01)

    def test_every_catalogue_column_resolves(self):
        cols = invoice_spec.item_columns(list(invoice_spec.ITEM_COLUMNS))
        self.assertEqual(len(invoice_spec.ITEM_COLUMNS), len(cols))
        for col in cols:
            self.assertTrue(col["label"])
            self.assertIn(col["align"], ("left", "num"))

    def test_unknown_column_keys_are_dropped_not_fatal(self):
        spec = invoice_spec.validate({"elements": [
            {"type": "items", "columns": ["bogus", "total", "total"]},
        ]})
        cols = [e for e in spec["elements"] if e["type"] == "items"][0]["columns"]
        self.assertNotIn("bogus", cols)
        self.assertEqual(1, cols.count("total"))

    def test_empty_column_list_falls_back_to_defaults(self):
        spec = invoice_spec.validate({"elements": [
            {"type": "items", "columns": []},
        ]})
        cols = [e for e in spec["elements"] if e["type"] == "items"][0]["columns"]
        self.assertEqual(list(invoice_spec.DEFAULT_ITEM_COLUMNS), cols)


class LegacyMigration(unittest.TestCase):
    """A template built in the old stacked builder must survive the upgrade."""

    def test_stacked_blocks_become_placed_elements(self):
        spec = invoice_spec.validate({"blocks": [
            {"type": "row", "children": [
                {"type": "logo"}, {"type": "title", "text": "SALES TAX INVOICE"}]},
            {"type": "row", "children": [
                {"type": "party", "side": "buyer", "heading": "Bill To"},
                {"type": "meta"}]},
            {"type": "items"}, {"type": "totals"}, {"type": "fbr"},
        ]})
        types = [e["type"] for e in spec["elements"]]
        for expected in ("logo", "header", "buyer", "meta", "items", "totals", "fbr"):
            self.assertIn(expected, types)

    def test_party_side_becomes_the_element_type(self):
        spec = invoice_spec.validate({"blocks": [
            {"type": "party", "side": "seller"},
            {"type": "party", "side": "buyer"},
        ]})
        types = [e["type"] for e in spec["elements"]]
        self.assertIn("seller", types)
        self.assertIn("buyer", types)

    def test_row_children_sit_side_by_side(self):
        spec = invoice_spec.validate({"blocks": [
            {"type": "row", "children": [{"type": "logo"}, {"type": "meta"}]},
        ]})
        placed = {e["type"]: e for e in spec["elements"]}
        self.assertEqual(placed["logo"]["y"], placed["meta"]["y"])
        self.assertLess(placed["logo"]["x"], placed["meta"]["x"])


class FieldResolution(unittest.TestCase):
    """The Invoice Field element and the shared invoice-details rows."""

    def setUp(self):
        self.data = {
            "invoiceRefNo": "0001", "invoiceDateDisplay": "01-Jan-2026",
            "PO": "PO-0001", "DN": "DN-0001", "CNIC": "DC-0001",
            "issueTime": "10:30 AM", "invoiceType": "Sale Invoice",
            "customFields": [{"label": "Vehicle No.", "value": "LEA-1234"}],
        }

    def test_meta_rows_include_po_dn_dc_and_custom_fields(self):
        labels = [r["label"] for r in meta_rows(self.data)]
        for expected in ("Invoice #", "P.O #", "D.N #", "D.C #", "Vehicle No."):
            self.assertIn(expected, labels)

    def test_meta_rows_omit_empty_values(self):
        labels = [r["label"] for r in meta_rows({"invoiceRefNo": "0001"})]
        self.assertEqual(["Invoice #"], labels)

    def test_meta_rows_prefer_a_profiles_resolved_rows(self):
        data = dict(self.data, infoRows=[
            {"key": "po", "label": "PURCHASE ORDER #", "value": "PO-9"},
        ])
        rows = meta_rows(data)
        self.assertEqual("PURCHASE ORDER #", rows[0]["label"])
        # Custom fields still append after the profile's rows.
        self.assertIn("Vehicle No.", [r["label"] for r in rows])

    def test_resolve_field_uses_the_catalogue_label_by_default(self):
        self.assertEqual("TIME OF ISSUE",
                         resolve_field(self.data, "time_of_issue")["label"])
        self.assertEqual("10:30 AM",
                         resolve_field(self.data, "time_of_issue")["value"])

    def test_resolve_field_honours_a_label_override(self):
        got = resolve_field(self.data, "invoice_type", "Document")
        self.assertEqual("Document", got["label"])
        self.assertEqual("Sale Invoice", got["value"])

    def test_resolve_field_follows_the_profiles_label(self):
        data = dict(self.data, infoRows=[
            {"key": "po", "label": "PURCHASE ORDER #", "value": "PO-9"},
        ])
        got = resolve_field(data, "po")
        self.assertEqual("PURCHASE ORDER #", got["label"])
        self.assertEqual("PO-9", got["value"])

    def test_unknown_field_key_is_empty_not_fatal(self):
        self.assertEqual("", resolve_field(self.data, "no_such_field")["value"])

    def test_every_field_key_is_placeable(self):
        """Each catalogue key must survive validation as a field element."""
        for key in invoice_spec.FIELD_KEYS:
            spec = invoice_spec.validate(
                {"elements": [{"type": "field", "key": key}]})
            placed = [e for e in spec["elements"] if e["type"] == "field"]
            self.assertEqual(1, len(placed), key)
            self.assertEqual(key, placed[0]["key"])


if __name__ == "__main__":
    unittest.main()
