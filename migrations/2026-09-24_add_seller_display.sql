-- Migration: let a letterhead carry the seller's identity
-- Date: 2026-09-24
--
-- A client whose letterhead already prints their company name and address does
-- not want the invoice to print them again immediately underneath. But the
-- seller's NTN/CNIC and STRN are a different matter: they are required on the
-- face of a sales tax invoice, and a letterhead almost never carries them.
--
-- So this is three-way rather than a boolean:
--
--   'full'     name, address and tax numbers   (the behaviour until now)
--   'tax_only' tax numbers only -- the letterhead supplies name and address
--   'hidden'   nothing -- only for a letterhead that prints NTN and STRN too
--
-- SAFETY: nullable with no default. NULL means 'full' for a client with no
-- letterhead image, and 'tax_only' for one who has imported a letterhead --
-- resolved in invoice_templates.resolve_theme, not here, so the rule stays
-- next to the templates that depend on it. No existing invoice changes unless
-- the client has a letterhead image, which before today they could only have
-- set deliberately.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_seller_display VARCHAR(16);

COMMENT ON COLUMN clients.tpl_seller_display IS
  'full | tax_only | hidden -- how much of the seller block to print under a letterhead; NULL = decide from whether a letterhead image is set';
