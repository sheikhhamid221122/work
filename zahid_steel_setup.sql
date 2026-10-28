-- ============================================================
-- Zahid Steel Industries (Pvt) Ltd
--   username  J527785
--   users.id  804a5a5a-9409-4883-abee-69881b9a0155
--
-- Run this once in pgAdmin against the live database. It does two things:
--
--   1. gives this client their own invoice template
--   2. puts their REAL NTN and STRN on the seller profile the invoice prints
--
-- Step 2 matters as much as step 1. The letterhead file they supplied carries
-- NTN 8255820-3 / STRN 32-7787-6203-99-6 -- which are ZAHID ENTERPRISES STEEL
-- RE-ROLLING MILLS' numbers, a SEPARATE business, not this one.
-- The template deliberately does NOT bake those numbers into the artwork -- it
-- prints whatever the payload carries -- so if the seller profile still holds
-- the old numbers, the sheet will print the old numbers.
--
--   correct NTN   J527785-7
--   correct STRN  3277876392575
--
-- No schema change is needed: nothing new was added for this client.
--
-- Everything here is scoped to this one client by users.id, and every step is
-- safe to run twice. No other client's rows are read or written.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. Give them their own invoice template
--
-- Keyed on users.id, which is the id you were given, and which is what
-- clients.user_id holds. It must report UPDATE 1. If it reports UPDATE 0 the
-- id is wrong -- run the SELECT at the foot of this file to find the right
-- one.
--
-- To roll back, set tpl_template back to NULL: the client falls straight back
-- to the per-username chain in app.py, unchanged.
-- ------------------------------------------------------------
UPDATE clients
   SET tpl_template = 'invoice_zahid_steel.html'
 WHERE user_id = '804a5a5a-9409-4883-abee-69881b9a0155';

-- ------------------------------------------------------------
-- 2a. The STRN on the clients row
--
-- app.py falls back to clients.strn before it looks at business_profiles, so
-- a stale value here wins over a corrected profile. Set both.
-- ------------------------------------------------------------
UPDATE clients
   SET strn = '3277876392575'
 WHERE user_id = '804a5a5a-9409-4883-abee-69881b9a0155';

-- ------------------------------------------------------------
-- 2b. The seller profile the create-invoice form pre-fills from
--
-- This is where sellerNTNCNIC, sellerSTRN and sellerAddress on every invoice
-- come from, and all three print on the sheet.
--
-- Only their DEFAULT profile is touched. A client may legitimately invoice as
-- more than one business, and rewriting the tax numbers on all of them would
-- be wrong.
-- ------------------------------------------------------------
UPDATE business_profiles bp
   SET business_name = 'ZAHID STEEL INDUSTRIES (PRIVATE) LIMITED',
       address       = 'Near Khokhar Village Ring Road, Badami Bagh, Lahore.',
       province      = 'Punjab',
       ntn_cnic      = 'J527785-7',
       strn          = '3277876392575'
  FROM clients c
 WHERE bp.client_id = c.id
   AND c.user_id = '804a5a5a-9409-4883-abee-69881b9a0155'
   AND bp.is_default;

-- If they have no profile at all yet -- a brand new client usually does not --
-- create the default one. HAVING NOT EXISTS is what makes a second run a
-- no-op; the UPDATE above has already corrected the row by then.
INSERT INTO business_profiles (client_id, business_name, address, province,
                               ntn_cnic, strn, is_default)
SELECT c.id,
       'ZAHID STEEL INDUSTRIES (PRIVATE) LIMITED',
       'Near Khokhar Village Ring Road, Badami Bagh, Lahore.',
       'Punjab',
       'J527785-7',
       '3277876392575',
       TRUE
  FROM clients c
 WHERE c.user_id = '804a5a5a-9409-4883-abee-69881b9a0155'
 -- GROUP BY is not optional here: HAVING makes this an aggregate query, and
 -- without it Postgres rejects c.id in the select list outright.
 GROUP BY c.id
HAVING NOT EXISTS (
           SELECT 1 FROM business_profiles x
            WHERE x.client_id = c.id
              AND x.is_default
       );

COMMIT;

-- ============================================================
-- Check it
-- ============================================================

-- Template assigned, and the tax numbers the sheet will print.
SELECT u.username,
       c.tpl_template,
       c.strn            AS clients_strn,
       bp.business_name,
       bp.ntn_cnic,
       bp.strn           AS profile_strn,
       bp.address
  FROM clients c
  JOIN users u             ON u.id = c.user_id
  LEFT JOIN business_profiles bp ON bp.client_id = c.id AND bp.is_default
 WHERE c.user_id = '804a5a5a-9409-4883-abee-69881b9a0155';

-- Expected:
--   username       J527785
--   tpl_template   invoice_zahid_steel.html
--   clients_strn   3277876392575
--   ntn_cnic       J527785-7
--   profile_strn   3277876392575

-- If the UPDATEs reported 0 rows, this finds the right id:
-- SELECT u.id, u.username, c.id AS client_id
--   FROM users u JOIN clients c ON c.user_id = u.id
--  WHERE u.username = 'J527785';

-- ------------------------------------------------------------
-- RESTART THE FLASK SERVER afterwards. LEGACY_TEMPLATES lives in Python
-- memory, so a running process does not know 'invoice_zahid_steel.html'
-- exists and will silently fall back to the default template.
-- ------------------------------------------------------------
