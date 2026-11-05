-- Migration: a supplier number and a delivery address on a buyer
-- Date: 2026-11-05
--
-- Both exist for the clients whose invoice replica prints them -- today only
-- Material Craft. Neither is an FBR field: app.py strips both out of the
-- payload before it is submitted (see PDF_ONLY_INVOICE_FIELDS), so they
-- travel with the invoice, are stored with it and are printed, and nothing
-- is ever filed from them.
--
-- Why they belong to the BUYER and not to the invoice. Both are properties
-- of the customer, not of the sale:
--
--   * the supplier number is the vendor number that buyer has assigned to
--     this client in their own purchase ledger -- Unilever call them
--     0000332375 and Friesland Campina call them 1006060 -- and it is what
--     the buyer matches the invoice against when paying it. It is the same
--     on every invoice to that buyer and different on every other one.
--   * the delivery address is the plant the goods go to, which is not the
--     buyer's billing address: Friesland Campina are invoiced at their head
--     office in Karachi and take delivery at Sahiwal Factory.
--
-- They began life as per-invoice custom fields and were moved here, so the
-- client picks a buyer and both fill themselves in instead of being retyped
-- on every sheet.
--
-- SAFETY: two additive columns, both nullable, no default and no backfill.
-- Every read of them is guarded by an information_schema check
-- (_column_exists in invoice_form_routes.py), so an app deployed ahead of
-- this migration simply does not offer the fields rather than erroring.
-- Nobody else's screens change: the fields are shown only for the clients in
-- client_features.BUYER_SUPPLIER_FIELDS.

ALTER TABLE buyers ADD COLUMN IF NOT EXISTS supplier_number VARCHAR(40);

COMMENT ON COLUMN buyers.supplier_number IS
  'The vendor number this buyer has assigned to the seller, as their invoice prints it. Not an FBR field and never submitted.';

-- Free text and roomier than the supplier number: a plant is written as a
-- name ("Sahiwal Factory") or as a full address, depending on the customer.
ALTER TABLE buyers ADD COLUMN IF NOT EXISTS delivery_address VARCHAR(200);

COMMENT ON COLUMN buyers.delivery_address IS
  'Where the goods are delivered, when that is not the buyer billing address. Printed on the invoice; not an FBR field and never submitted.';
