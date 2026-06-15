-- Migration: Convert template_settings JSONB to individual columns
-- Date: 2026-03-03
-- Purpose: Make settings configurable via Supabase checkboxes instead of JSON

-- =====================================================
-- HEADER SETTINGS
-- =====================================================
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_header_color VARCHAR(10) DEFAULT 'dark';
-- Values: 'dark', 'blue', 'white'

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_top_spacing INTEGER DEFAULT 0;
-- Number of line breaks before content (for letterhead)

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_logo_width INTEGER DEFAULT 220;
-- Logo width in pixels

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_logo_height INTEGER;
-- Logo height in pixels (NULL means auto)

ALTER TABLE clients ADD COLUMN IF NOT EXISTS invoice_custom_field_names JSONB DEFAULT '[]'::jsonb;
-- Default reusable custom field labels for create-invoice form

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_seller_strn BOOLEAN DEFAULT true;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_seller_ntn BOOLEAN DEFAULT true;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_seller_address BOOLEAN DEFAULT true;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_fbr_invoice_header BOOLEAN DEFAULT true;

-- =====================================================
-- BUYER SECTION SETTINGS
-- =====================================================
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_buyer_strn BOOLEAN DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_status BOOLEAN DEFAULT true;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_po BOOLEAN DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_dc BOOLEAN DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_cnic BOOLEAN DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_hs_code_buyer BOOLEAN DEFAULT false;

-- =====================================================
-- PRODUCTS TABLE SETTINGS
-- =====================================================
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_product_code BOOLEAN DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_show_hs_code BOOLEAN DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_apply_further_tax BOOLEAN DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_max_item_rows INTEGER DEFAULT 6;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_fixed_tax_rate VARCHAR(10) DEFAULT '18%';

-- =====================================================
-- COMMENTS for Supabase UI hints
-- =====================================================
COMMENT ON COLUMN clients.tpl_header_color IS 'Header color: dark, blue, or white';
COMMENT ON COLUMN clients.tpl_top_spacing IS 'Line breaks before content (0-5) for letterhead';
COMMENT ON COLUMN clients.tpl_logo_width IS 'Logo width in pixels';
COMMENT ON COLUMN clients.tpl_logo_height IS 'Logo height in pixels; NULL means auto';
COMMENT ON COLUMN clients.invoice_custom_field_names IS 'Default reusable custom field labels for the create invoice form';
COMMENT ON COLUMN clients.tpl_show_seller_strn IS 'Show STRN in header';
COMMENT ON COLUMN clients.tpl_show_seller_ntn IS 'Show NTN in header';
COMMENT ON COLUMN clients.tpl_show_seller_address IS 'Show address in header';
COMMENT ON COLUMN clients.tpl_show_fbr_invoice_header IS 'Show FBR Invoice # in header';
COMMENT ON COLUMN clients.tpl_show_buyer_strn IS 'Show BUYER STRN row';
COMMENT ON COLUMN clients.tpl_show_status IS 'Show Registered/Unregistered status';
COMMENT ON COLUMN clients.tpl_show_po IS 'Show P.O # field';
COMMENT ON COLUMN clients.tpl_show_dc IS 'Show DC # field';
COMMENT ON COLUMN clients.tpl_show_cnic IS 'Show CNIC field';
COMMENT ON COLUMN clients.tpl_show_hs_code_buyer IS 'Show HS Code in buyer section';
COMMENT ON COLUMN clients.tpl_show_product_code IS 'Show Product Code column';
COMMENT ON COLUMN clients.tpl_show_hs_code IS 'Show HS Code column in products table';
COMMENT ON COLUMN clients.tpl_apply_further_tax IS 'Show Further Tax (4%) column';
COMMENT ON COLUMN clients.tpl_max_item_rows IS 'Number of item rows to display';
COMMENT ON COLUMN clients.tpl_fixed_tax_rate IS 'Tax rate label (e.g., 18%)';
