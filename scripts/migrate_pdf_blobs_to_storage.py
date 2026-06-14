import argparse
import json
import os
import sys

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from storage import get_storage  # noqa: E402


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT"),
    )


def invoice_filename(invoice_id, invoice_data_raw):
    fallback = f"invoice_{invoice_id}.pdf"
    try:
        invoice_data = json.loads(invoice_data_raw) if isinstance(invoice_data_raw, str) else invoice_data_raw
    except Exception:
        return fallback

    if not isinstance(invoice_data, dict):
        return fallback

    ref = invoice_data.get("invoiceRefNo") or invoice_data.get("fbrInvoiceNumber") or fallback[:-4]
    return f"{ref}.pdf"


def migrate(batch_size, dry_run):
    storage = get_storage()
    conn = get_db_connection()
    migrated = 0

    try:
        while True:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, pdf_data, invoice_data
                FROM invoices
                WHERE pdf_data IS NOT NULL
                  AND pdf_url IS NULL
                ORDER BY id
                LIMIT %s
                """,
                (batch_size,),
            )
            rows = cur.fetchall()
            cur.close()

            if not rows:
                break

            for invoice_id, pdf_data, invoice_data_raw in rows:
                filename = invoice_filename(invoice_id, invoice_data_raw)
                file_bytes = bytes(pdf_data)

                if dry_run:
                    print(f"[dry-run] invoice {invoice_id}: would write {len(file_bytes)} bytes as {filename}")
                    migrated += 1
                    continue

                file_url = storage.save(file_bytes, filename)
                update_cur = conn.cursor()
                update_cur.execute(
                    "UPDATE invoices SET pdf_url = %s WHERE id = %s AND pdf_url IS NULL",
                    (file_url, invoice_id),
                )
                update_cur.close()
                conn.commit()
                print(f"invoice {invoice_id}: {file_url}")
                migrated += 1

            if dry_run:
                break
    finally:
        conn.close()

    print(f"{'Would migrate' if dry_run else 'Migrated'} {migrated} invoice PDFs.")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Move invoice PDF blobs from invoices.pdf_data into configured storage. "
            "This script requires the legacy invoices.pdf_data column to still exist."
        )
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    migrate(args.batch_size, args.dry_run)


if __name__ == "__main__":
    main()
