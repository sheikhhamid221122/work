-- Migration: Add per-client universal invoice FBR buyer-section visibility
-- Date: 2026-06-06
-- Purpose: Allow showing FBR Invoice # in the buyer info section instead of status

ALTER TABLE clients
ADD COLUMN IF NOT EXISTS tpl_show_fbr_invoice_buyer BOOLEAN DEFAULT false;

UPDATE clients
SET tpl_show_fbr_invoice_buyer = false
WHERE tpl_show_fbr_invoice_buyer IS NULL;

COMMENT ON COLUMN clients.tpl_show_fbr_invoice_buyer IS 'Show FBR Invoice # in buyer section instead of status';
