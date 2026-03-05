-- Migration: Add template configuration columns to clients table
-- Date: 2026-03-02
-- Purpose: Enable database-driven template configuration for new clients
--          Existing clients remain unaffected (they use default/existing templates)

-- Step 1: Add template_type column for quick template selection
-- Values: 'default' (existing behavior), 'universal' (new template), 'nologo', 'innovative', etc.
ALTER TABLE clients 
ADD COLUMN IF NOT EXISTS template_type VARCHAR(50) DEFAULT 'default';

-- Step 2: Add template_settings for fine-grained control (JSON)
-- This allows per-client customization without code changes
ALTER TABLE clients 
ADD COLUMN IF NOT EXISTS template_settings JSONB DEFAULT '{}';

-- Step 3: Add comment explaining the template_settings structure
COMMENT ON COLUMN clients.template_settings IS 'JSON settings for invoice template customization.
===========================================
COMPLETE SETTINGS REFERENCE (all optional):
===========================================

HEADER SECTION:
  "header_color": "dark"          -- Header background color: "dark" (default), "blue", "white"
  "top_spacing": 0                -- Number of <br> before content (0-5) for letterhead space
  "show_seller_info": true        -- Show entire seller info section
  "show_seller_strn": true        -- Show STRN in header (auto-hides if empty)
  "show_seller_ntn": true         -- Show NTN in header
  "show_seller_address": true     -- Show address in header
  "show_seller_name_with_logo": false -- Show business name text even when logo exists
  "show_fbr_invoice_header": true -- Show FBR Invoice # in header
  "logo_width": 220               -- Logo width in pixels
  "logo_height": null             -- Logo height (null = auto)

BUYER SECTION:
  "show_buyer_strn": false        -- Show BUYER STRN row
  "show_status": true             -- Show Registered/Unregistered status
  "show_fbr_invoice_buyer": false -- Show FBR Invoice # in buyer section (instead of status)
  "show_po": false                -- Show P.O # field
  "show_dn": false                -- Show D.N. No. field
  "show_dc": false                -- Show DC # field
  "show_cnic": false              -- Show CNIC field
  "show_hs_code_buyer": false     -- Show HS Code in buyer section (for single-product invoices)
  "show_purchase_order": false    -- Show PURCHASE ORDER # (verbose label)
  "show_delivery_challan": false  -- Show DELIVERY CHALLAN # (verbose label)

PRODUCTS TABLE:
  "show_product_code": false      -- Add Product Code column
  "show_hs_code": false           -- Add HS Code column in products table
  "show_sales_tax_rate_column": false -- Show separate Tax Rate column per item
  "apply_further_tax": false      -- Show Further Tax (4%) column for unregistered buyers
  "fixed_tax_rate": "18%"         -- Default tax rate display text
  "max_item_rows": 6              -- Number of rows to display (fills empty rows)

STYLING:
  "border_color": "#999999"       -- Border color for tables
';

-- =====================================================
-- EXAMPLE CONFIGURATIONS FOR DIFFERENT CLIENT TYPES
-- =====================================================

-- EXAMPLE 1: Basic client (minimal settings - most fields hidden)
-- Good for clients with simple invoices
-- INSERT INTO clients (user_id, name, template_type, template_settings)
-- VALUES (123, 'Basic Client', 'universal', '{}');

-- EXAMPLE 2: Client with logo, shows STRN, PO/DC fields
-- INSERT INTO clients (user_id, name, template_type, template_settings)
-- VALUES (123, 'Standard Client', 'universal', '{
--   "show_buyer_strn": true,
--   "show_po": true,
--   "show_dc": true,
--   "max_item_rows": 8
-- }');

-- EXAMPLE 3: Trading company with Product Codes and HS Codes
-- Similar to Innovative Trading template
-- INSERT INTO clients (user_id, name, template_type, template_settings)
-- VALUES (123, 'Trading Company', 'universal', '{
--   "show_product_code": true,
--   "show_hs_code": true,
--   "show_dc": true,
--   "max_item_rows": 6
-- }');

-- EXAMPLE 4: Client with Further Tax calculation
-- Similar to template3 behavior
-- INSERT INTO clients (user_id, name, template_type, template_settings)
-- VALUES (123, 'Further Tax Client', 'universal', '{
--   "apply_further_tax": true,
--   "show_buyer_strn": true,
--   "max_item_rows": 6
-- }');

-- EXAMPLE 5: Client without STRN (no STRN registered)
-- STRN will auto-hide even if show_seller_strn is true
-- INSERT INTO clients (user_id, name, template_type, template_settings)
-- VALUES (123, 'No STRN Client', 'universal', '{
--   "show_seller_strn": false
-- }');

-- EXAMPLE 6: Client with CNIC field requirement
-- Similar to zeeshan_st template
-- INSERT INTO clients (user_id, name, template_type, template_settings)
-- VALUES (123, 'CNIC Required Client', 'universal', '{
--   "show_cnic": true,
--   "show_buyer_strn": true,
--   "top_spacing": 2
-- }');

-- EXAMPLE 7: Blue header style with HS Code in buyer section
-- Similar to zahid trading template
-- INSERT INTO clients (user_id, name, template_type, template_settings)
-- VALUES (123, 'Blue Header Client', 'universal', '{
--   "header_color": "blue",
--   "show_hs_code_buyer": true,
--   "show_status": false
-- }');

-- EXAMPLE 8: Custom logo size
-- INSERT INTO clients (user_id, name, template_type, template_settings)
-- VALUES (123, 'Custom Logo Client', 'universal', '{
--   "logo_width": 280,
--   "show_seller_name_with_logo": true
-- }');

-- =====================================================
-- TO UPDATE AN EXISTING CLIENT TO USE NEW TEMPLATE:
-- =====================================================
-- UPDATE clients 
-- SET template_type = 'universal',
--     template_settings = '{"show_status": true, "show_buyer_strn": true}'
-- WHERE id = <client_id>;

-- =====================================================
-- EXISTING CLIENTS: No changes needed!
-- template_type defaults to 'default' which uses existing username-based templates
-- They continue using template_type = 'default' which
-- preserves their current template selection in app.py
-- =====================================================
