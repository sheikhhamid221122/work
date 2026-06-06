-- Migration: Add per-client universal invoice top-header visibility
-- Date: 2026-06-06
-- Purpose: Allow hiding the entire top header block in invoice_template_universal.html

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_top_header BOOLEAN DEFAULT true;

COMMENT ON COLUMN clients.tpl_show_top_header IS 'Show universal invoice top header with logo/seller details';
