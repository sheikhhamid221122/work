"""
Tests for the migrations runner.

There is no database here, so what is under test is the runner's decisions:
the order files are applied in, that an applied file is not applied twice,
that a failure rolls back and stops rather than carrying on into the next
file, and that --force re-runs. Executing the SQL itself is Postgres's job.

The migrations in this project are all written to be safe to re-run
(ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, a guarded DO block, and
one UPDATE restricted to rows that are still NULL). A test asserts that, so a
future migration that is not re-runnable has to be a deliberate choice.

Run:  python3 -m unittest discover -s tests -v
"""

import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import migrate  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.conn.statements.append(text)
        if self.conn.fail_on and self.conn.fail_on in text:
            raise RuntimeError("boom")
        if text.startswith("SELECT filename FROM schema_migrations"):
            self.rows = [(name,) for name in sorted(self.conn.applied)]
        elif text.startswith("INSERT INTO schema_migrations"):
            self.conn.pending_ledger.append(params[0])
            self.rows = []
        else:
            self.rows = []

    def fetchall(self):
        return list(getattr(self, "rows", []))


class FakeConnection:
    def __init__(self, applied=(), fail_on=None):
        self.applied = set(applied)
        self.statements = []
        self.pending_ledger = []
        self.fail_on = fail_on
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1
        self.applied.update(self.pending_ledger)
        self.pending_ledger = []

    def rollback(self):
        self.rollbacks += 1
        self.pending_ledger = []

    def close(self):
        pass


class Runner(unittest.TestCase):

    def run_migrate(self, conn, argv):
        original_connect, original_argv = migrate.connect, sys.argv
        migrate.connect = lambda dsn=None: conn
        sys.argv = ["migrate.py"] + argv
        try:
            return migrate.main()
        finally:
            migrate.connect, sys.argv = original_connect, original_argv

    def test_files_are_applied_in_date_order(self):
        files = migrate.migration_files()
        self.assertEqual(sorted(files), files)
        # The names lead with an ISO date, which is what makes sorting correct.
        for name in files:
            self.assertRegex(name, r"^\d{4}-\d{2}-\d{2}_.+\.sql$")

    def test_a_fresh_database_applies_everything(self):
        conn = FakeConnection()
        self.assertEqual(0, self.run_migrate(conn, []))
        self.assertEqual(set(migrate.migration_files()), conn.applied)

    def test_an_applied_migration_is_not_applied_twice(self):
        files = migrate.migration_files()
        conn = FakeConnection(applied=files)
        self.assertEqual(0, self.run_migrate(conn, []))
        # Nothing but the ledger bootstrap and the SELECT should have run.
        bodies = [s for s in conn.statements
                  if "schema_migrations" not in s]
        self.assertEqual([], bodies)

    def test_force_reapplies_everything(self):
        files = migrate.migration_files()
        conn = FakeConnection(applied=files)
        self.assertEqual(0, self.run_migrate(conn, ["--force"]))
        inserts = [s for s in conn.statements
                   if s.startswith("INSERT INTO schema_migrations")]
        self.assertEqual(len(files), len(inserts))

    def test_status_changes_nothing(self):
        conn = FakeConnection()
        self.assertEqual(0, self.run_migrate(conn, ["--status"]))
        self.assertEqual(set(), conn.applied)
        inserts = [s for s in conn.statements if s.startswith("INSERT")]
        self.assertEqual([], inserts)

    def test_a_failure_rolls_back_and_stops(self):
        # Fail on the very first migration's distinctive text.
        first = migrate.migration_files()[0]
        with io.open(os.path.join(migrate.MIGRATIONS, first),
                     encoding="utf-8") as handle:
            marker = " ".join(handle.read().split())[:40]

        conn = FakeConnection(fail_on=marker)
        self.assertEqual(1, self.run_migrate(conn, []))
        self.assertEqual(1, conn.rollbacks)
        self.assertNotIn(first, conn.applied)
        # And it did not carry on into the next file.
        self.assertEqual([], [s for s in conn.statements
                              if s.startswith("INSERT INTO schema_migrations")])


    def test_print_needs_no_database_at_all(self):
        # The route for someone sitting in pgAdmin with no credentials on the
        # command line: it must never try to connect.
        def explode(dsn=None):
            raise AssertionError("--print must not open a connection")

        original_connect, original_argv = migrate.connect, sys.argv
        migrate.connect = explode
        sys.argv = ["migrate.py", "--print"]
        buffer = io.StringIO()
        stdout = sys.stdout
        sys.stdout = buffer
        try:
            code = migrate.main()
        finally:
            sys.stdout = stdout
            migrate.connect, sys.argv = original_connect, original_argv

        self.assertEqual(0, code)
        printed = buffer.getvalue()
        for name in migrate.migration_files():
            self.assertIn(name, printed)
        self.assertIn("tpl_letterhead_url", printed)

    def test_dsn_is_passed_through(self):
        seen = {}

        def capture(dsn=None):
            seen["dsn"] = dsn
            return FakeConnection(applied=migrate.migration_files())

        original_connect, original_argv = migrate.connect, sys.argv
        migrate.connect = capture
        sys.argv = ["migrate.py", "--dsn", "postgresql://u:p@h:5432/db"]
        try:
            migrate.main()
        finally:
            migrate.connect, sys.argv = original_connect, original_argv
        self.assertEqual("postgresql://u:p@h:5432/db", seen["dsn"])


class MigrationsAreRerunnable(unittest.TestCase):
    """Every migration must survive being applied twice.

    The runner keeps a ledger, but a ledger can be lost or a database restored
    from before it existed, and --force is offered as a repair. Both are only
    safe while every file is genuinely idempotent.
    """

    DESTRUCTIVE = re.compile(
        r"^\s*(DROP|TRUNCATE|DELETE)\b", re.IGNORECASE | re.MULTILINE)

    def statements(self, name):
        path = os.path.join(migrate.MIGRATIONS, name)
        with io.open(path, encoding="utf-8") as handle:
            body = handle.read()
        # Strip comments; several files carry long explanatory headers.
        return re.sub(r"--[^\n]*", "", body)

    def test_no_migration_drops_or_deletes(self):
        for name in migrate.migration_files():
            self.assertIsNone(self.DESTRUCTIVE.search(self.statements(name)),
                              f"{name} contains a destructive statement")

    def test_every_add_column_is_guarded(self):
        for name in migrate.migration_files():
            body = self.statements(name)
            for match in re.finditer(r"ADD COLUMN(?!\s+IF NOT EXISTS)", body,
                                     re.IGNORECASE):
                # The one exception is an ADD COLUMN inside a DO block that has
                # already checked information_schema for itself.
                self.assertIn("information_schema", body,
                              f"{name} has an unguarded ADD COLUMN at "
                              f"offset {match.start()}")

    def test_every_data_write_is_restricted_to_new_rows(self):
        # An UPDATE in a migration is only re-runnable if it cannot touch a row
        # a user has since edited. `WHERE ... IS NULL` is that guarantee.
        for name in migrate.migration_files():
            body = self.statements(name)
            for match in re.finditer(r"UPDATE\s+\w+(.*?);", body,
                                     re.IGNORECASE | re.DOTALL):
                self.assertRegex(
                    " ".join(match.group(1).split()), r"(?i)WHERE .*IS NULL",
                    f"{name} has an UPDATE that could overwrite existing data")


if __name__ == "__main__":
    unittest.main()
