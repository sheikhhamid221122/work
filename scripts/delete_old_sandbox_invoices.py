import argparse
import os

import psycopg2
from dotenv import load_dotenv


SANDBOX_ENV = "sandbox"


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT"),
    )


def cleanup_old_sandbox_invoices(days, dry_run):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if dry_run:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM invoices
                WHERE env = %s
                  AND created_at < NOW() - (%s * INTERVAL '1 day')
                """,
                (SANDBOX_ENV, days),
            )
            count = cur.fetchone()[0]
            print(
                f"[dry-run] Would delete {count} sandbox invoices older than {days} days."
            )
            return count

        cur.execute(
            """
            DELETE FROM invoices
            WHERE env = %s
              AND created_at < NOW() - (%s * INTERVAL '1 day')
            """,
            (SANDBOX_ENV, days),
        )
        deleted = cur.rowcount
        conn.commit()
        print(f"Deleted {deleted} sandbox invoices older than {days} days.")
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Delete old sandbox rows from the invoices table."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Delete sandbox invoices older than this many days. Default: 30.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count matching rows without deleting them.",
    )
    args = parser.parse_args()

    if args.days < 1:
        raise ValueError("--days must be at least 1")

    load_dotenv()
    cleanup_old_sandbox_invoices(args.days, args.dry_run)


if __name__ == "__main__":
    main()
