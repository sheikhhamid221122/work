-- Migration: remembered sale-person names, and a phone number on a buyer
-- Date: 2026-10-20
--
-- Both exist for the clients whose invoice replica prints them -- today only
-- Salon Marketings (username 3520223163127). Neither is an FBR field: app.py
-- strips both out of the payload before it is submitted (see
-- PDF_ONLY_INVOICE_FIELDS), so they travel with the invoice, are stored with
-- it and are printed, and nothing is ever filed from them.
--
-- SAFETY: two additive columns, both with harmless defaults. Every read of
-- them is guarded by an information_schema check (_column_exists in
-- invoice_form_routes.py), so an app deployed ahead of this migration simply
-- does not offer the fields rather than erroring. Nobody else's screens
-- change: the fields are shown only for the usernames in
-- SALES_PERSON_USERNAMES.

-- The names this client has typed into "Sale Person", so the same handful can
-- be picked again instead of retyped. A new name is added automatically when
-- an invoice is created with it. A list, not a table: a shop has a few sales
-- staff, and this matches how clients.invoice_custom_field_names already
-- remembers its defaults.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS sales_persons JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN clients.sales_persons IS
  'Sale-person names this client has used, newest last; offered on the invoice form. Printed only, never filed with FBR.';

-- The buyer's phone number as their invoice prints it. Free text on purpose:
-- Pakistani numbers are written half a dozen ways and this one is printed,
-- never dialled or filed, so validating a shape would only get in the way.
ALTER TABLE buyers ADD COLUMN IF NOT EXISTS phone VARCHAR(40);

COMMENT ON COLUMN buyers.phone IS
  'Buyer phone number for the invoice sheet. Not an FBR field and never submitted.';
