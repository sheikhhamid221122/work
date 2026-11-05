-- ============================================================
-- Material Craft
--   id given  71a52649-1b77-4748-9a51-fd33ce621000
--
-- Run this once in pgAdmin against the live database. Steps 1 and 2 are
-- safe and complete the invoice build. Step 3 is SEPARATE and must not be
-- run until the question in its header has been answered -- read it.
--
-- BEFORE THIS SCRIPT: apply the migration. The Supplier # and Delivery
-- address fields on the Buyers page need two new columns:
--
--     python scripts/migrate.py
--
-- (it applies migrations/2026-11-05_add_buyer_supplier_number_and_
-- delivery_address.sql). Until it runs, the app simply does not offer
-- those two fields -- every read of them is guarded by an
-- information_schema check, so nothing errors, the invoice just prints
-- both rows empty.
--
-- A note on the id. The application identifies this client by the id above
-- (client_features.py), and it accepts that id as either users.id or
-- clients.id -- a UUID is unique across both and the code does not care
-- which it is. This script does the same: every statement matches a client
-- whose users.id OR whose clients.id is that value, so it is correct
-- whichever one you were given. Step 0 tells you which it turned out to be.
--
-- Their invoice prints on their own pre-printed letterpad: the template
-- leaves the top 21mm and the bottom 23mm of every page empty for it.
-- That is in the template, not in any row here.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 0. Who this is
--
-- Read this before committing. It must return exactly ONE row. If it returns
-- none, the id is wrong and the UPDATEs below will report UPDATE 0 --
-- roll back and find the right id with the SELECT at the foot of this file.
-- ------------------------------------------------------------
SELECT u.id      AS user_id,
       c.id      AS client_id,
       u.username,
       c.tpl_template AS template_before,
       c.invoice_custom_field_names AS custom_fields_before
  FROM clients c
  JOIN users u ON u.id = c.user_id
 WHERE u.id = '71a52649-1b77-4748-9a51-fd33ce621000'
    OR c.id = '71a52649-1b77-4748-9a51-fd33ce621000';

-- ------------------------------------------------------------
-- 1. Give them their own invoice template
--
-- Must report UPDATE 1.
--
-- To roll back, set tpl_template back to NULL: the client falls straight
-- back to the per-username chain in app.py, unchanged.
-- ------------------------------------------------------------
UPDATE clients c
   SET tpl_template = 'invoice_material_craft.html'
  FROM users u
 WHERE u.id = c.user_id
   AND (u.id = '71a52649-1b77-4748-9a51-fd33ce621000'
        OR c.id = '71a52649-1b77-4748-9a51-fd33ce621000');

-- ------------------------------------------------------------
-- 2. Pre-fill the four custom field names
--
-- These are the names only -- the values are typed per invoice. The form
-- loads them from /api/invoice-custom-field-defaults and fills the four
-- rows in, so nobody retypes the labels on every sheet. They print in the
-- grid at the top right, beside Invoice #.
--
-- FOUR, not five. Delivery Address was a fifth custom field in the first
-- cut and has moved onto the BUYER, along with Supplier #, because both
-- are properties of the customer rather than of the sale. They are filled
-- in on the Buyers page now, once per customer, and fill themselves in on
-- the invoice when that customer is picked. Nothing to seed here for
-- them -- the client types each buyer's values on the Buyers page.
--
-- Written only when the client has not set their own names, so re-running
-- this never stands on something they changed afterwards. To reset them,
-- set invoice_custom_field_names to NULL and run this again.
-- ------------------------------------------------------------
UPDATE clients c
   SET invoice_custom_field_names =
         '["P.O #", "P.O Date", "D Challan #", "DC Date"]'::jsonb
  FROM users u
 WHERE u.id = c.user_id
   AND (u.id = '71a52649-1b77-4748-9a51-fd33ce621000'
        OR c.id = '71a52649-1b77-4748-9a51-fd33ce621000')
   AND (c.invoice_custom_field_names IS NULL
        OR c.invoice_custom_field_names = '[]'::jsonb);

-- ------------------------------------------------------------
-- 3. Check, then COMMIT
--
-- Expected: template_after = invoice_material_craft.html and
-- custom_fields_after = ["P.O #", "P.O Date", "D Challan #", "DC Date"].
-- ------------------------------------------------------------
SELECT u.username,
       c.tpl_template AS template_after,
       c.invoice_custom_field_names AS custom_fields_after
  FROM clients c
  JOIN users u ON u.id = c.user_id
 WHERE u.id = '71a52649-1b77-4748-9a51-fd33ce621000'
    OR c.id = '71a52649-1b77-4748-9a51-fd33ce621000';

COMMIT;
-- ROLLBACK;   -- use this instead if step 0 or step 3 looked wrong


-- ============================================================
-- STEP 4 -- THE SELLER NTN.  DO NOT RUN THIS YET.
--
-- Their sheet shows N.T.N 1270230-7. Their business profile holds
-- 3520229157309, which is their CNIC 35202-2915730-9 with the dashes
-- stripped, and that is what is being FILED as sellerNTNCNIC today.
--
-- Why this needs confirming first. On the invoice they sent, the FBR
-- invoice number reads
--
--     3520229157309DI39CBBP165272
--
-- FBR builds that number from the seller's own registration, so on the
-- evidence of their own paperwork FBR currently knows this business by the
-- CNIC, not by NTN 1270230. Change the profile and the next submission
-- goes up under a different identifier: at best their FBR invoice numbers
-- change shape from here on, at worst the submission is rejected.
--
-- So: ask them (or their FBR portal) which identifier their Digital
-- Invoicing registration is under. Run this ONLY if the answer is 1270230.
--
-- If the answer is the CNIC, do NOT run it. Tell me instead and the sheet
-- can print the NTN while the CNIC keeps being filed -- a different and
-- more delicate change, because the printed document and the filed record
-- would then state different identifiers.
--
-- The template needs no change either way. It prints whatever the profile
-- holds, and adds the "-7" check digit only to the bare 1270230 -- a digit
-- that is DERIVED from those seven characters, so the sheet and the filing
-- still state the same number.
-- ------------------------------------------------------------
-- BEGIN;
--
-- UPDATE business_profiles bp
--    SET ntn_cnic = '1270230'
--   FROM clients c
--   JOIN users u ON u.id = c.user_id
--  WHERE bp.client_id = c.id
--    AND (u.id = '71a52649-1b77-4748-9a51-fd33ce621000'
--         OR c.id = '71a52649-1b77-4748-9a51-fd33ce621000')
--    AND bp.ntn_cnic = '3520229157309';
--
-- SELECT bp.business_name, bp.ntn_cnic, bp.strn, bp.is_default
--   FROM business_profiles bp
--   JOIN clients c ON c.id = bp.client_id
--   JOIN users u ON u.id = c.user_id
--  WHERE u.id = '71a52649-1b77-4748-9a51-fd33ce621000'
--     OR c.id = '71a52649-1b77-4748-9a51-fd33ce621000';
--
-- COMMIT;
-- ============================================================


-- ============================================================
-- If step 0 returned no rows, find the client by name instead:
--
--   SELECT u.id AS user_id, c.id AS client_id, u.username, u.name
--     FROM users u JOIN clients c ON u.id = c.user_id
--    WHERE u.name ILIKE '%material%craft%'
--       OR u.username ILIKE '%1270230%';
--
-- The seller GST prints live from the payload, which comes from
-- business_profiles / clients.strn. It should hold
--
--   STRN  0300127023012      (printed as 03-00-1270-230-12)
--
-- If the sheet prints something else, that is the row to fix -- not the
-- template. The template regroups only those exact thirteen digits.
--
-- Supplier # and Delivery address are per-BUYER now. Set them on the
-- Buyers page, one customer at a time -- for example Unilever 0000332375,
-- Friesland Campina 1006060 / Sahiwal Factory. They are printed only and
-- never submitted to FBR.
-- ============================================================
