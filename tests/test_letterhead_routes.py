"""
Tests for the two letterhead-import endpoints.

These cover what the unit tests cannot: that the round trip actually stores a
letterhead, that the reservation written to the database is the same number of
millimetres the band was cut at, and that the rendered source page handed to
the browser between the two steps cannot be pointed at another client's file.

The database is stubbed. Nothing here needs a real one -- what is under test is
the routing, the ownership check and the columns written.

Run:  python3 -m unittest discover -s tests -v
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

from flask import Flask
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import letterhead_import as li          # noqa: E402
import template_routes                  # noqa: E402

CLIENT_ID = 42
OTHER_CLIENT = 43


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        if "information_schema" in text:
            self.rows = [(column,) for column in template_routes.SETTING_COLUMNS]
        elif text.startswith("UPDATE clients"):
            self.db["updates"].append((text, list(params or [])))
            self.rows = []
        elif "FROM business_profiles" in text:
            self.rows = []
        elif "FROM fbr" in text:
            self.rows = [(None,)]
        elif "logo_url FROM clients" in text:
            self.rows = [(None,)]
        elif "FROM clients" in text:
            self.rows = [tuple(None for _ in template_routes.SETTING_COLUMNS)]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        self.db["commits"] += 1

    def rollback(self):
        pass

    def close(self):
        pass


def sample_invoice_png():
    """A page with a clear masthead and a clear gap beneath it."""
    image = Image.new("RGB", (620, 877), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 620, int(40 / 297 * 877)], fill=(11, 61, 98))
    draw.rectangle([30, int(70 / 297 * 877), 590, int(80 / 297 * 877)], fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class LetterheadImportRoutes(unittest.TestCase):

    def setUp(self):
        # The screens and their API are off in the product (see
        # template_routes.EXPOSE_TEMPLATE_UI). These tests cover the feature
        # itself, so they turn it on explicitly and put it back afterwards --
        # which also means the flag cannot be flipped without the suite
        # continuing to prove the feature still works behind it.
        self._ui_was = template_routes.EXPOSE_TEMPLATE_UI
        template_routes.EXPOSE_TEMPLATE_UI = True
        self.db = {"updates": [], "commits": 0}
        self.workdir = tempfile.mkdtemp()
        self.origin = os.getcwd()
        # The routes write into ./static/uploads; keep that out of the repo.
        os.chdir(self.workdir)

        # Real template and static folders, so the page routes render too --
        # the uploads still land in the temp cwd, which is what matters.
        app = Flask(__name__, root_path=ROOT, template_folder="templates",
                    static_folder="static")
        app.secret_key = "test"
        app.config["TESTING"] = True
        template_routes.add_template_routes(
            app, lambda: FakeConnection(self.db), lambda text: "")
        self.app = app
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["client_id"] = CLIENT_ID
            sess["username"] = "tester"

    def tearDown(self):
        template_routes.EXPOSE_TEMPLATE_UI = self._ui_was
        os.chdir(self.origin)
        shutil.rmtree(self.workdir, ignore_errors=True)

    # ------------------------------------------------------------- helpers
    def _render(self, blob=None, filename="invoice.png"):
        return self.client.post(
            "/api/letterhead/import",
            data={"file": (io.BytesIO(blob or sample_invoice_png()), filename)},
            content_type="multipart/form-data")

    def _commit(self, source, mm):
        return self.client.post(
            "/api/letterhead/import/commit",
            data=json.dumps({"source": source, "mm": mm}),
            content_type="application/json")

    def _written(self):
        """The columns and values of the last UPDATE, as a dict."""
        text, params = self.db["updates"][-1]
        assignments = text.split("SET", 1)[1].split("WHERE", 1)[0]
        columns = [part.split("=")[0].strip() for part in assignments.split(",")]
        return dict(zip(columns, params))

    # --------------------------------------------------------------- tests
    def test_render_step_returns_a_measured_suggestion(self):
        response = self._render()
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["detected"])
        self.assertGreater(body["suggested_mm"], li.MIN_BAND_MM)
        self.assertLessEqual(body["suggested_mm"], body["max_mm"])
        self.assertTrue(body["source_url"].startswith("/static/uploads/letterheads/"))
        self.assertTrue(os.path.exists(
            os.path.join("static", "uploads", "letterheads", body["source"])))

    def test_commit_writes_the_same_millimetres_it_cropped(self):
        source = self._render().get_json()["source"]
        body = self._commit(source, 48).get_json()

        self.assertEqual(48, body["mm"])
        written = self._written()
        self.assertEqual(48, written["tpl_letterhead_mm"])
        self.assertTrue(written["tpl_letterhead_fullbleed"])
        self.assertTrue(written["tpl_letterhead_enabled"])
        self.assertEqual(body["url"], written["tpl_letterhead_url"])

        # And the stored image really is that band of the page.
        path = os.path.join("static", "uploads", "letterheads",
                            os.path.basename(body["url"]))
        with Image.open(path) as crop:
            self.assertAlmostEqual(48, crop.height / 877 * 297, delta=1)

    def test_commit_consumes_the_source_page(self):
        source = self._render().get_json()["source"]
        path = os.path.join("static", "uploads", "letterheads", source)
        self.assertTrue(os.path.exists(path))
        self._commit(source, 40)
        self.assertFalse(os.path.exists(path),
                         "the rendered page should not outlive the import")

    def test_a_new_import_clears_the_abandoned_one(self):
        first = self._render().get_json()["source"]
        self._render()
        self.assertFalse(
            os.path.exists(os.path.join("static", "uploads", "letterheads", first)),
            "an abandoned source page should not linger")

    def test_commit_refuses_another_clients_source(self):
        # The file really exists on disk -- what stops the commit is that the
        # name carries a different client's token, not that it is missing.
        source = self._render().get_json()["source"]
        token = source.rsplit("-", 1)[1][:-4]
        stolen = li.source_name(OTHER_CLIENT, token)
        self.assertNotEqual(source, stolen)

        response = self._commit(stolen, 40)
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self.db["updates"])
        self.assertTrue(os.path.exists(
            os.path.join("static", "uploads", "letterheads", source)),
            "a refused commit must not consume the real source either")

    def test_commit_refuses_a_traversal_path(self):
        self._render()
        for source in ("../../app.py", "/etc/passwd", "", None):
            response = self._commit(source, 40)
            self.assertEqual(400, response.status_code, source)
        self.assertEqual([], self.db["updates"])

    def test_commit_clamps_an_out_of_range_request(self):
        source = self._render().get_json()["source"]
        body = self._commit(source, 9999).get_json()
        self.assertEqual(int(li.bounds_mm(297.0)[1]), body["mm"])

    def test_unreadable_upload_is_a_client_error_not_a_crash(self):
        response = self._render(blob=b"not an image", filename="invoice.png")
        self.assertEqual(400, response.status_code)
        self.assertIn("error", response.get_json())

    def test_both_steps_require_a_session(self):
        anonymous = self.app.test_client()
        self.assertEqual(401, anonymous.post("/api/letterhead/import").status_code)
        self.assertEqual(401, anonymous.post(
            "/api/letterhead/import/commit",
            data="{}", content_type="application/json").status_code)

    def test_manual_upload_clears_full_bleed(self):
        # Full-bleed means "this is a crop of the client's own page". A
        # hand-uploaded graphic that inherited it would be stretched.
        self.client.post(
            "/api/letterhead",
            data={"file": (io.BytesIO(sample_invoice_png()), "header.png")},
            content_type="multipart/form-data")
        self.assertIs(False, self._written()["tpl_letterhead_fullbleed"])

    def test_a_migration_applied_under_a_running_server_takes_effect(self):
        """Applying a migration must not require restarting the app.

        The columns to SELECT are read from information_schema, and caching
        that forever means a server started before the migration keeps reading
        the old set -- so the client runs the migration, sees no change, and
        reasonably concludes it did not work. The cache is therefore only kept
        once every column is present.
        """
        missing = "tpl_letterhead_url"
        url = "/static/uploads/letterheads/proof.png"
        state = {"migrated": False}
        original = FakeCursor.execute

        def patched(cursor, sql, params=None):
            body = " ".join(sql.split())
            if "information_schema" in body:
                names = list(template_routes.SETTING_COLUMNS)
                if not state["migrated"]:
                    names.remove(missing)
                cursor.rows = [(name,) for name in names]
                return
            if body.startswith("SELECT") and "FROM clients" in body:
                # Answer with whatever columns this SELECT actually named, so
                # a stale column list produces a stale answer -- exactly what
                # it would do against a real database.
                selected = body[len("SELECT "):body.index(" FROM")].split(", ")
                cursor.rows = [tuple(url if c == missing else None
                                     for c in selected)]
                return
            original(cursor, sql, params)

        FakeCursor.execute = patched
        try:
            # Before the migration the letterhead column is not selected, so
            # the page cannot know about it.
            before = self.client.get("/settings/templates").get_data(as_text=True)
            self.assertNotIn(url, before)

            state["migrated"] = True

            # Next request, same process, no restart.
            after = self.client.get("/settings/templates").get_data(as_text=True)
            self.assertIn(url, after,
                          "a migration applied mid-process should be picked up")
        finally:
            FakeCursor.execute = original

    def test_a_complete_schema_is_only_inspected_once(self):
        """The catalogue read must not become a per-request cost."""
        reads = []
        original = FakeCursor.execute

        def patched(cursor, sql, params=None):
            if "information_schema" in " ".join(sql.split()):
                reads.append(1)
            original(cursor, sql, params)

        FakeCursor.execute = patched
        try:
            for _ in range(3):
                self.client.get("/settings/templates")
        finally:
            FakeCursor.execute = original
        self.assertEqual(1, len(reads), "the column list should be cached")

    def test_a_pending_migration_is_reported_as_an_instruction(self):
        """The failure everyone hits on first deploy must be actionable.

        Postgres says `column "tpl_letterhead_url" of relation "clients" does
        not exist`, which tells a client nothing they can act on. What they
        need is the command that fixes it.
        """
        import psycopg2.errors

        source = self._render().get_json()["source"]

        def refuse(sql, params=None):
            if " ".join(sql.split()).startswith("UPDATE clients"):
                raise psycopg2.errors.UndefinedColumn(
                    'column "tpl_letterhead_url" of relation "clients" '
                    'does not exist')
        original = FakeCursor.execute
        FakeCursor.execute = lambda self, sql, params=None: (
            refuse(sql, params) or original(self, sql, params))
        try:
            response = self._commit(source, 40)
        finally:
            FakeCursor.execute = original

        self.assertEqual(500, response.status_code)
        body = response.get_json()
        self.assertIn("scripts/migrate.py", body["error"])
        self.assertNotIn("relation", body["error"],
                         "the raw Postgres text belongs in the log, not the UI")

    def test_removing_the_image_clears_full_bleed(self):
        self.client.delete("/api/letterhead")
        written = self._written()
        self.assertIsNone(written["tpl_letterhead_url"])
        self.assertIs(False, written["tpl_letterhead_fullbleed"])


if __name__ == "__main__":
    unittest.main()
