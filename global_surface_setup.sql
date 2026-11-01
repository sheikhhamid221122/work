-- ============================================================
-- Global Surface Technologies
--   id given  ed156fdd-086e-480c-be18-a2c85d9e31b1
--
-- Run this once in pgAdmin against the live database. It does two things:
--
--   1. gives this client their own invoice template
--   2. pre-fills the four custom field names their sheet carries, so the
--      Create Invoice form offers PO Number / PO Date / DC Number / DC Date
--      instead of two blank rows
--
-- A note on the id. The application identifies this client by the id above
-- (client_features.py), and it accepts that id as either users.id or
-- clients.id -- a UUID is unique across both and the code does not care
-- which it is. This script does the same: every statement matches a client
-- whose users.id OR whose clients.id is that value, so it is correct
-- whichever one you were given. Step 0 tells you which it turned out to be.
--
-- There is NO schema change here. The only new column these changes read --
-- buyers.phone, for the buyer Phone row -- was added by
-- migrations/2026-10-20_add_sales_persons_and_buyer_phone.sql for Salon
-- Marketings. If that migration has not been applied on this database the
-- Phone field simply does not appear anywhere (every read of it is guarded
-- by an information_schema check), and nothing breaks. Apply it with
-- `python scripts/migrate.py` if the field is missing.
--
-- Everything here is scoped to this one client, and every step is safe to
-- run twice. No other client's rows are read or written.
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
 WHERE u.id = 'ed156fdd-086e-480c-be18-a2c85d9e31b1'
    OR c.id = 'ed156fdd-086e-480c-be18-a2c85d9e31b1';

-- ------------------------------------------------------------
-- 1. Give them their own invoice template
--
-- Must report UPDATE 1.
--
-- To roll back, set tpl_template back to NULL: the client falls straight
-- back to the per-username chain in app.py, unchanged.
-- ------------------------------------------------------------
UPDATE clients c
   SET tpl_template = 'invoice_global_surface.html'
  FROM users u
 WHERE u.id = c.user_id
   AND (u.id = 'ed156fdd-086e-480c-be18-a2c85d9e31b1'
        OR c.id = 'ed156fdd-086e-480c-be18-a2c85d9e31b1');

-- ------------------------------------------------------------
-- 2. Pre-fill the four custom field names
--
-- These are the names only -- the values are typed per invoice. The form
-- loads them from /api/invoice-custom-field-defaults and fills the four
-- rows in, so nobody retypes the labels on every sheet.
--
-- Written only when the client has not set their own names, so re-running
-- this never stands on something they changed afterwards. To reset them,
-- set invoice_custom_field_names to NULL and run this again.
--
-- Four names, not two: the cap is raised for this client in
-- client_features.py. Without that code deployed the API would truncate
-- this list back to two on the next read.
-- ------------------------------------------------------------
UPDATE clients c
   SET invoice_custom_field_names =
         '["PO Number", "PO Date", "DC Number", "DC Date"]'::jsonb
  FROM users u
 WHERE u.id = c.user_id
   AND (u.id = 'ed156fdd-086e-480c-be18-a2c85d9e31b1'
        OR c.id = 'ed156fdd-086e-480c-be18-a2c85d9e31b1')
   AND (c.invoice_custom_field_names IS NULL
        OR c.invoice_custom_field_names = '[]'::jsonb);

-- ------------------------------------------------------------
-- 3. Check, then COMMIT
--
-- Expected: template_after = invoice_global_surface.html and
-- custom_fields_after = ["PO Number", "PO Date", "DC Number", "DC Date"].
-- ------------------------------------------------------------
SELECT u.username,
       c.tpl_template AS template_after,
       c.invoice_custom_field_names AS custom_fields_after
  FROM clients c
  JOIN users u ON u.id = c.user_id
 WHERE u.id = 'ed156fdd-086e-480c-be18-a2c85d9e31b1'
    OR c.id = 'ed156fdd-086e-480c-be18-a2c85d9e31b1';

COMMIT;
-- ROLLBACK;   -- use this instead if step 0 or step 3 looked wrong

-- ============================================================
-- If step 0 returned no rows, find the client by name instead:
--
--   SELECT u.id AS user_id, c.id AS client_id, u.username, u.name
--     FROM users u JOIN clients c ON u.id = c.user_id
--    WHERE u.name ILIKE '%global%surface%'
--       OR u.username ILIKE '%2229304%';
--
-- The seller NTN and STRN print live from the FBR payload, which comes from
-- business_profiles / clients.strn. They should already read
--
--   NTN   2229304-3
--   STRN  3277876189796
--
-- If the sheet prints something else, that is the row to fix -- not the
-- template. (The template falls back to the two numbers above only when the
-- payload carries none at all, so a WRONG number in the profile still
-- prints; only a missing one is covered.)
-- ============================================================
