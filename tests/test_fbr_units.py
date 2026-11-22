"""
Tests for the unit-of-measure vocabulary: it is FBR's, or it is nothing.

The form used to offer thirty-five units that were a guess at FBR's
vocabulary rather than a copy of it -- "LTR" where FBR says "Litre", short
codes FBR does not use for a dozen others -- hardcoded twice, once in
/api/form-options and once as FALLBACK_UOMS in fbr_reference_routes. A
line built from one of them was accepted by the form, saved, and then
rejected by FBR at submission with nothing on screen to explain it.

So these tests are mostly about what must NOT come back: no second list
anywhere, and nothing invented when FBR cannot be reached.

No database and no FBR token: the token lookup and the HTTP call are the
two seams, and both are replaced here.

Run:  python -m unittest discover -s tests -v
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402

import fbr_reference_routes as ref  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What FBR actually answers with: a description and an id, nothing else.
FBR_RESPONSE = [
    {"uoM_ID": 77, "description": "Square Metre"},
    {"uoM_ID": 82, "description": "Litre"},
    {"uoM_ID": 85, "description": "KG"},
]


def app_context(client_id="client-a"):
    app = Flask(__name__)
    app.secret_key = "test"
    ctx = app.test_request_context("/")
    ctx.push()
    from flask import session
    session["client_id"] = client_id
    session["env"] = "sandbox"
    return ctx


class StubFbr:
    """Stands in for the token lookup and the HTTP call."""

    def __init__(self, token="t0ken", payload=None, error=None):
        self.token = token
        self.payload = payload
        self.error = error
        self.calls = []

    def __enter__(self):
        self._token_fn = ref.get_client_fbr_token
        self._get_fn = ref.fbr_get
        ref.get_client_fbr_token = lambda *a, **k: ("https://gw.fbr.gov.pk", self.token)

        def fake_get(endpoint, token, params=None):
            self.calls.append((endpoint, params))
            if self.error:
                return None, self.error
            return self.payload, None

        ref.fbr_get = fake_get
        return self

    def __exit__(self, *exc):
        ref.get_client_fbr_token = self._token_fn
        ref.fbr_get = self._get_fn
        return False


class NormalizeUoms(unittest.TestCase):
    def test_fbrs_description_becomes_the_posted_value(self):
        # uoM is posted as a string, and the string has to be FBR's own word.
        # An earlier version read `description` into the label and a short
        # code into the value, which is how "KG - Kilogram" reached FBR.
        rows = ref.normalize_uoms(FBR_RESPONSE)
        self.assertEqual([r["value"] for r in rows], ["Square Metre", "Litre", "KG"])
        for row in rows:
            self.assertEqual(row["value"], row["description"])

    def test_the_id_is_carried_through(self):
        self.assertEqual([r["uoM_ID"] for r in ref.normalize_uoms(FBR_RESPONSE)],
                         [77, 82, 85])

    def test_blank_and_duplicate_descriptions_are_dropped(self):
        rows = ref.normalize_uoms([
            {"uoM_ID": 1, "description": "Litre"},
            {"uoM_ID": 2, "description": "  "},
            {"uoM_ID": 3, "description": "Litre"},
            {"uoM_ID": 4, "description": None},
            "not a dict",
        ])
        self.assertEqual([r["description"] for r in rows], ["Litre"])

    def test_surrounding_space_is_stripped(self):
        rows = ref.normalize_uoms([{"uoM_ID": 1, "description": " Litre "}])
        self.assertEqual(rows[0]["value"], "Litre")


class FetchUoms(unittest.TestCase):
    def setUp(self):
        ref._clear_cache()
        self.ctx = app_context()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(ref._clear_cache)

    def test_fbrs_list_comes_back_as_the_api_source(self):
        with StubFbr(payload=FBR_RESPONSE):
            uoms, source = ref.fetch_uoms(None, lambda: "sandbox")
        self.assertEqual(source, "api")
        self.assertEqual([u["value"] for u in uoms], ["Square Metre", "Litre", "KG"])

    def test_no_token_gives_nothing_and_says_so(self):
        # The old behaviour was to hand back the invented list under the
        # source "fallback", which read on screen exactly like FBR's answer.
        with StubFbr(token=None):
            uoms, source = ref.fetch_uoms(None, lambda: "sandbox")
        self.assertEqual((uoms, source), ([], "unavailable"))

    def test_an_api_error_gives_nothing_and_says_so(self):
        with StubFbr(error="502 Bad Gateway"):
            uoms, source = ref.fetch_uoms(None, lambda: "sandbox")
        self.assertEqual((uoms, source), ([], "unavailable"))

    def test_an_empty_answer_is_not_dressed_up_as_a_list(self):
        with StubFbr(payload=[]):
            uoms, source = ref.fetch_uoms(None, lambda: "sandbox")
        self.assertEqual((uoms, source), ([], "unavailable"))

    def test_the_second_call_is_served_from_cache(self):
        with StubFbr(payload=FBR_RESPONSE) as stub:
            ref.fetch_uoms(None, lambda: "sandbox")
            uoms, source = ref.fetch_uoms(None, lambda: "sandbox")
        self.assertEqual(source, "cache")
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(stub.calls[0][0], "/pdi/v1/uom")

    def test_one_clients_list_is_not_served_to_another(self):
        # Each client has its own FBR token and so its own answer; the cache
        # key carries the client id for that reason. Without it, the first
        # client through the door would decide what the next one is offered.
        with StubFbr(payload=FBR_RESPONSE) as stub:
            ref.fetch_uoms(None, lambda: "sandbox")
            other = app_context("client-b")
            try:
                _, source = ref.fetch_uoms(None, lambda: "sandbox")
            finally:
                other.pop()
        self.assertEqual(source, "api", "client-b was served from client-a's cache")
        self.assertEqual(len(stub.calls), 2)

    def test_nothing_is_cached_when_fbr_could_not_be_reached(self):
        # A cached emptiness would last 24 hours and look exactly like FBR
        # saying it has no units.
        with StubFbr(error="timeout"):
            ref.fetch_uoms(None, lambda: "sandbox")
        with StubFbr(payload=FBR_RESPONSE) as stub:
            uoms, source = ref.fetch_uoms(None, lambda: "sandbox")
        self.assertEqual(source, "api")
        self.assertEqual(len(stub.calls), 1)


class NoSecondList(unittest.TestCase):
    """Guards against the hardcoded vocabulary being pasted back in."""

    # The tells of the old list: short codes FBR does not use, and the
    # "CODE - Long name" labels that were posted verbatim to FBR.
    INVENTED = ['"LTR"', "'LTR'", '"LTR - Liter"', '"MT - Metric Ton"',
                '"KG - Kilogram"', '"KWH - Kilowatt Hour"']

    SOURCES = ["fbr_reference_routes.py", "invoice_form_routes.py",
               "master_data_routes.py", "master_data.py",
               os.path.join("templates", "create-invoice.html"),
               os.path.join("templates", "products.html"),
               os.path.join("static", "js", "master-data.js")]

    def read(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_fallback_list_is_gone_for_good(self):
        self.assertFalse(hasattr(ref, "FALLBACK_UOMS"),
                         "FALLBACK_UOMS is back; see fetch_uoms for why there "
                         "is deliberately no offline unit list")
        self.assertNotIn("FALLBACK_UOMS", self.read("fbr_reference_routes.py"))

    def test_no_file_offers_a_unit_of_its_own(self):
        found = []
        for name in self.SOURCES:
            body = self.read(name)
            for token in self.INVENTED:
                if token in body:
                    found.append("%s contains %s" % (name, token))
        self.assertEqual([], found)

    def test_form_options_serves_what_fbr_said(self):
        body = self.read("invoice_form_routes.py")
        self.assertIn("fetch_uoms", body)
        self.assertIn('"uoms": uoms_data', body)
        self.assertIn('"uomsSource": uoms_source', body)

    def test_both_reference_endpoints_normalise_through_one_function(self):
        # 5.6 and 5.9 answer in the same shape. Two copies of the mapping is
        # how one of them came to put a label where FBR expects its own word.
        body = self.read("fbr_reference_routes.py")
        self.assertEqual(1, body.count("def normalize_uoms("))
        self.assertEqual(2, body.count("normalized = normalize_uoms("),
                         "both UOM endpoints should normalise through it")

    def test_an_unreachable_fbr_is_reported_not_papered_over(self):
        body = self.read("fbr_reference_routes.py")
        self.assertIn('"source": "unavailable"', body)
        self.assertIn('"source": "unrestricted"', body)


class ImportBlamesTheRightThing(unittest.TestCase):
    """The bulk product import checks units against FBR's list and nothing
    else, so it must not blame the spreadsheet when that list is missing."""

    def read(self):
        with open(os.path.join(ROOT, "master_data_routes.py"), encoding="utf-8") as fh:
            return fh.read()

    def test_an_unavailable_list_stops_the_import_with_its_own_message(self):
        body = self.read()
        self.assertIn("FBR's list of units could not be reached", body)
        # Before the row loop, not inside it: every row would otherwise fail
        # with "is not an FBR unit of measure", which blames the file.
        start = body.index("    def products_import():")
        guard = body.index("FBR's list of units could not be reached", start)
        loop = body.index("for number, values in rows:", start)
        self.assertLess(guard, loop)

    def test_the_template_is_not_built_without_its_dropdown(self):
        body = self.read()
        self.assertEqual(2, body.count("FBR's list of units could not be reached"),
                         "the import and the template download both need it")


class UnitOfMeasureInTheUi(unittest.TestCase):
    """What the invoice form does with each of the three answers."""

    def read(self):
        with open(os.path.join(ROOT, "templates", "create-invoice.html"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_the_box_is_no_longer_seeded_from_form_options_directly(self):
        body = self.read()
        self.assertNotIn("populateSelect(productUom, state.formOptions.uoms)", body)

    def test_a_saved_unit_fbr_does_not_know_is_kept_and_marked(self):
        body = self.read()
        self.assertIn("function ensureUomOption(", body)
        self.assertIn("(not an FBR unit)", body)
        # Every place a saved unit is applied goes through it, rather than
        # assigning .value and silently doing nothing when it is not listed.
        self.assertNotIn("document.getElementById('product-uom').value = p.uom", body)

    def test_an_unreachable_list_empties_the_box_rather_than_filling_it(self):
        body = self.read()
        self.assertIn("function showUomsUnavailable(", body)
        self.assertIn("FBR unit list unavailable", body)

    def test_the_units_are_fetched_while_the_code_is_typed(self):
        # Waiting for blur meant the unit arrived after they had already
        # looked at the box.
        body = self.read()
        self.assertIn("looksLikeCompleteHsCode", body)
        self.assertIn("hsUomTimeout", body)

    def test_the_three_sources_are_handled_separately(self):
        body = self.read()
        for source in ("'api'", "'unrestricted'"):
            self.assertIn("data.source === " + source, body)


if __name__ == "__main__":
    unittest.main()
