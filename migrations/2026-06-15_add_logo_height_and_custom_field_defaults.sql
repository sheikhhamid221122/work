-- Migration: Add logo height and reusable invoice custom field defaults
-- Date: 2026-06-15

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_logo_height INTEGER;

COMMENT ON COLUMN clients.tpl_logo_height IS 'Logo height in pixels; NULL means auto';
