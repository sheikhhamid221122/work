ALTER TABLE invoices
ADD COLUMN IF NOT EXISTS pdf_url TEXT;

CREATE INDEX IF NOT EXISTS idx_invoices_pdf_url
ON invoices (pdf_url)
WHERE pdf_url IS NOT NULL;
