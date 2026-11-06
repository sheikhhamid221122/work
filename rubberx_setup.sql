-- ============================================================
-- Rubber X Technologies
--   id given  af5d3ee4-f799-49a0-84db-813e18fdb9a7
--
-- Run this once in pgAdmin against the live database. Step 1 is the whole
-- job -- it assigns the template that was built for them. Step 2 is
-- optional and seeds the two reference-field names their sheet carries.
--
-- BEFORE THIS SCRIPT: deploy the code. The template is unreachable until
-- the new filename is in LEGACY_TEMPLATES, which lives in Python memory --
-- a running Flask process will not know it exists and will silently fall
-- back to the default. So: deploy, RESTART FLASK, then run this. app.py
-- prints "Selected template: ..." on every render; watch for
-- invoice_rubberx.html.
--
-- A note on the id. A UUID is unique across both tables and the
-- application does not care which of the two it was given, so every
-- statement here matches a client whose users.id OR whose clients.id is
-- that value. It is therefore correct whichever one you were handed.
-- Step 0 tells you which it turned out to be.
--
-- Nothing in this script touches their seller profile. The invoice prints
-- the address, NTN and Sales Tax No. live from the payload, which comes
-- from business_profiles / clients -- see the note at the foot of this
-- file for what those rows should hold.
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
       u.name,
       c.tpl_template AS template_before,
       c.invoice_custom_field_names AS custom_fields_before
  FROM clients c
  JOIN users u ON u.id = c.user_id
 WHERE u.id = 'af5d3ee4-f799-49a0-84db-813e18fdb9a7'
    OR c.id = 'af5d3ee4-f799-49a0-84db-813e18fdb9a7';

-- ------------------------------------------------------------
-- 1. Give them their own invoice template
--
-- Must report UPDATE 1.
--
-- To roll back, set tpl_template back to NULL: the client falls straight
-- back to the per-username chain in app.py, unchanged.
-- ------------------------------------------------------------
UPDATE clients c
   SET tpl_template = 'invoice_rubberx.html'
  FROM users u
 WHERE u.id = c.user_id
   AND (u.id = 'af5d3ee4-f799-49a0-84db-813e18fdb9a7'
        OR c.id = 'af5d3ee4-f799-49a0-84db-813e18fdb9a7');

-- ------------------------------------------------------------
-- 2. OPTIONAL -- pre-fill the two reference field names
--
-- Their sheet types one reference line, "PONO". The template already
-- prints a P.O. No. row from the invoice form's own PO field and a
-- D. Challan No. row from its DN field, each only when that field is
-- filled in, so this step is NOT needed for either of those to appear.
--
-- What it seeds is the two CUSTOM field names, which print under the FBR
-- invoice number in the order entered. Their sheet carries a typed
-- "Dated-" line beside the PO number, and time of supply is theirs to
-- state, so those are the two worth having pre-labelled. The names only --
-- the values are typed per invoice.
--
-- Written only when the client has not set their own names, so re-running
-- this never stands on something they changed afterwards. To reset them,
-- set invoice_custom_field_names to NULL and run this again. Skip this
-- step entirely if they would rather name the two fields themselves.
-- ------------------------------------------------------------
UPDATE clients c
   SET invoice_custom_field_names = '["P.O Date", "Time of Supply"]'::jsonb
  FROM users u
 WHERE u.id = c.user_id
   AND (u.id = 'af5d3ee4-f799-49a0-84db-813e18fdb9a7'
        OR c.id = 'af5d3ee4-f799-49a0-84db-813e18fdb9a7')
   AND (c.invoice_custom_field_names IS NULL
        OR c.invoice_custom_field_names = '[]'::jsonb);

-- ------------------------------------------------------------
-- 3. Check, then COMMIT
--
-- Expected: template_after = invoice_rubberx.html, and
-- custom_fields_after = ["P.O Date", "Time of Supply"] if step 2 was run.
-- ------------------------------------------------------------
SELECT u.username,
       c.tpl_template AS template_after,
       c.invoice_custom_field_names AS custom_fields_after
  FROM clients c
  JOIN users u ON u.id = c.user_id
 WHERE u.id = 'af5d3ee4-f799-49a0-84db-813e18fdb9a7'
    OR c.id = 'af5d3ee4-f799-49a0-84db-813e18fdb9a7';

COMMIT;
-- ROLLBACK;   -- use this instead if step 0 or step 3 looked wrong


-- ============================================================
-- If step 0 returned no rows, find the client by name instead:
--
--   SELECT u.id AS user_id, c.id AS client_id, u.username, u.name
--     FROM users u JOIN clients c ON u.id = c.user_id
--    WHERE u.name ILIKE '%rubber%'
--       OR u.username ILIKE '%3786648%';
--
--
-- WHAT THE SHEET PRINTS LIVE, AND WHERE IT COMES FROM
--
-- The template prints these from the invoice payload, which is built from
-- business_profiles / clients -- NOT from anything baked into the artwork.
-- If the printed sheet shows the wrong value, the row is what to fix, not
-- the template. From the invoice they supplied, these should read:
--
--   business name   Rubber x Technologies
--   address         Shop No 1 Basement Haq Plaza, 72 Rail Way Road Lahore
--   NTN             3786648-6
--   Sales Tax No.   0300378664816
--
-- Two of those are worth a second look before anyone "corrects" them,
-- because their own sheet is the only source for either:
--
--   * The Sales Tax No. reads 0300378664816 on their invoice. Thirteen
--     digits is the right shape for an STRN, but it is also exactly their
--     mobile number with a digit on the end, so confirm it against their
--     FBR portal rather than assuming the sheet is right.
--   * Their NTN prints as typed. Check which identifier their Digital
--     Invoicing registration is actually under before changing the
--     profile -- FBR builds the FBR invoice number from the seller's
--     registration, so filing under a different one changes the shape of
--     every number they get from here on. (This is the trap Material Craft
--     hit; see material_craft_setup.sql, step 4.)
--
-- The phone and fax numbers are NOT in the payload. They are fixed text in
-- templates/invoice_rubberx.html -- change them there if theirs change:
--
--   Phone: 92-423 5067598     Fax: 92-423 7651783
--
-- So is the right-hand signature caption, "Muhamad Basit / Owner", and the
-- services line in the footer.
-- ============================================================
