-- ============================================================
-- Material Craft
--   id given  71a52649-1b77-4748-9a51-fd33ce621000
--
-- Run this once in pgAdmin against the live database. It does two things:
--
--   1. gives this client their own invoice template
--   2. pre-fills the five custom field names their sheet carries, so the
--      Create Invoice form offers P.O # / P.O Date / D Challan # / DC Date
--      / Delivery Address instead of two blank rows
--
-- A note on the id. The application identifies this client by the id above
-- (client_features.py), and it accepts that id as either users.id or
-- clients.id -- a UUID is unique across both and the code does not care
-- which it is. This script does the same: every statement matches a client
-- whose users.id OR whose clients.id is that value, so it is correct
-- whichever one you were given. Step 0 tells you which it turned out to be.
--
-- There is NO schema change here, and nothing outside this one client's
-- rows is read or written. Every step is safe to run twice.
--
-- Their invoice prints on their own pre-printed letterpad: the template
-- leaves the top 19mm and the bottom 23mm of every page empty for it. That
-- is in the template, not in any row here, so there is nothing to set for
-- it -- but it is the reason their sheets must be printed on their own
-- paper rather than plain A4.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 0. Who this is
--
-- Read this before committing. It must return exactly ONE row. If it returns
-- none, the id is wrong and the two UPDATEs below will report UPDATE 0 --
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
-- 2. Pre-fill the five custom field names
--
-- These are the names only -- the values are typed per invoice. The form
-- loads them from /api/invoice-custom-field-defaults and fills the five
-- rows in, so nobody retypes the labels on every sheet.
--
-- Four of them print in the grid at the top right, beside Invoice #. The
-- fifth, Delivery Address, prints in the buyer identifier box on the left:
-- the template picks it out BY NAME, so this spelling matters. Rename it on
-- the form and the plant stops going in that row and starts appearing as a
-- fifth line in the grid instead.
--
-- Written only when the client has not set their own names, so re-running
-- this never stands on something they changed afterwards. To reset them,
-- set invoice_custom_field_names to NULL and run this again.
--
-- Five names, not two: the cap is raised for this client in
-- client_features.py. Without that code deployed the API would truncate
-- this list back to two on the next read.
-- ------------------------------------------------------------
UPDATE clients c
   SET invoice_custom_field_names =
         '["P.O #", "P.O Date", "D Challan #", "DC Date", "Delivery Address"]'::jsonb
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
-- custom_fields_after = ["P.O #", "P.O Date", "D Challan #", "DC Date",
-- "Delivery Address"].
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
-- If step 0 returned no rows, find the client by name instead:
--
--   SELECT u.id AS user_id, c.id AS client_id, u.username, u.name
--     FROM users u JOIN clients c ON u.id = c.user_id
--    WHERE u.name ILIKE '%material%craft%'
--       OR u.username ILIKE '%1270230%';
--
-- The seller NTN and GST print live from the FBR payload, which comes from
-- business_profiles / clients.strn. For their sheet to read the way their
-- own does, those rows should hold
--
--   NTN   1270230-7
--   STRN  0300127023012      (printed as 03-00-1270-230-12)
--
-- If the sheet prints something else, that is the row to fix -- not the
-- template. The template falls back to those two values only when the
-- payload carries none at all, so a WRONG number in the profile still
-- prints; only a missing one is covered.
--
-- Supplier # 0000332375 and CNIC # 35202-2915730-9 are not FBR fields --
-- the payload has no element for either -- so they are in the template
-- verbatim and there is no row to set for them.
-- ============================================================
