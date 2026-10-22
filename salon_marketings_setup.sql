-- ============================================================
-- Salon Marketings (username 3520223163127,
--                   users.id 4f2424c7-8812-4ee6-b85b-79bb73b49388)
--
-- Run this once in pgAdmin against the live database. It does four things:
--
--   1. adds the two columns the new fields need
--   2. records the migration so scripts/migrate.py does not redo it
--   3. gives this client their own invoice template
--   4. creates their default "ABC / Walk-in Customer" buyer
--
-- Everything here is scoped to this one client by username, and every step is
-- safe to run twice: the columns use IF NOT EXISTS, the ledger row uses ON
-- CONFLICT DO NOTHING, and the buyer is only inserted if it is not there yet.
-- No other client's rows are read or written.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. Schema  (= migrations/2026-10-20_add_sales_persons_and_buyer_phone.sql)
-- ------------------------------------------------------------

-- The sale-person names this client has typed, so the same handful can be
-- picked again instead of retyped. A new name is added automatically when an
-- invoice is created with it -- nothing needs to be seeded here.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS sales_persons JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN clients.sales_persons IS
  'Sale-person names this client has used, newest last; offered on the invoice form. Printed only, never filed with FBR.';

-- The buyer's phone number as their invoice prints it. Free text on purpose:
-- it is printed, never dialled or filed.
ALTER TABLE buyers ADD COLUMN IF NOT EXISTS phone VARCHAR(40);

COMMENT ON COLUMN buyers.phone IS
  'Buyer phone number for the invoice sheet. Not an FBR field and never submitted.';

-- ------------------------------------------------------------
-- 2. Record it in the same ledger scripts/migrate.py uses
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (filename)
VALUES ('2026-10-20_add_sales_persons_and_buyer_phone.sql')
ON CONFLICT (filename) DO NOTHING;

-- ------------------------------------------------------------
-- 3. Give Salon Marketings their own invoice template
--
-- Keyed on users.id, which is the id you were given, and which is what
-- clients.user_id holds. NOT on users.username: if that number is their NTN
-- rather than their login, a username match finds nothing and this whole
-- script succeeds while changing nothing at all.
--
-- It must report UPDATE 1. If it reports UPDATE 0, the id is wrong -- run
-- the SELECT at the foot of this file to find the right one.
-- ------------------------------------------------------------
UPDATE clients
   SET tpl_template = 'invoice_salon_marketings.html'
 WHERE user_id = '4f2424c7-8812-4ee6-b85b-79bb73b49388';

-- ------------------------------------------------------------
-- 4. Their default buyer: ABC / Walk-in Customer
--
-- NTN 9999997 and province Punjab are there because the system requires them
-- (Lahore is in Punjab -- the field is a province, not a city). Neither is
-- printed: their template shows only Customer, Phone, Address, and the NTN
-- line that a sales tax invoice has to carry.
-- ------------------------------------------------------------

-- Only one buyer per client may be the default, so any other default of
-- theirs is stood down first. The walk-in buyer itself is excluded: on a
-- second run this statement would otherwise clear the flag on the row the
-- INSERT below had already created, and the INSERT would then skip it,
-- leaving the client with no default buyer at all.
UPDATE buyers b
   SET is_default = FALSE
  FROM clients c
 WHERE b.client_id = c.id
   AND c.user_id = '4f2424c7-8812-4ee6-b85b-79bb73b49388'
   AND b.is_default
   AND LOWER(BTRIM(b.business_name)) <> 'abc / walk-in customer';

INSERT INTO buyers (client_id, business_name, address, province, ntn_cnic,
                    strn, registration_type, buyer_code, is_default, phone)
SELECT c.id,
       'ABC / Walk-in Customer',
       'ABC',
       'Punjab',
       '9999997',
       '',
       'Unregistered',
       -- Next code in this client's own B-0001, B-0002, ... series, the same
       -- way master_data.reserve_codes assigns one.
       'B-' || LPAD((
           COALESCE(MAX(CAST(SUBSTRING(UPPER(BTRIM(b.buyer_code))
                             FROM '^B-([0-9]{1,9})$') AS BIGINT)), 0) + 1
       )::text, 4, '0'),
       TRUE,
       '123-'
  FROM clients c
  LEFT JOIN buyers b ON b.client_id = c.id
 WHERE c.user_id = '4f2424c7-8812-4ee6-b85b-79bb73b49388'
 GROUP BY c.id
HAVING NOT EXISTS (
           SELECT 1 FROM buyers x
            WHERE x.client_id = c.id
              AND LOWER(BTRIM(x.business_name)) = 'abc / walk-in customer'
       );

-- And make sure it is the default even if the row was already there from an
-- earlier run, or was added by hand without the flag.
UPDATE buyers b
   SET is_default = TRUE
  FROM clients c
 WHERE b.client_id = c.id
   AND c.user_id = '4f2424c7-8812-4ee6-b85b-79bb73b49388'
   AND LOWER(BTRIM(b.business_name)) = 'abc / walk-in customer'
   AND NOT b.is_default;

COMMIT;

-- ============================================================
-- Check afterwards
--
-- FIRST, and the one that matters if the invoice still prints the default
-- sheet. It also tells you their real username, which is what decides
-- whether the Sale Person and Phone fields appear on the form:
--
--   SELECT u.id AS user_id, u.username, c.id AS client_id, c.tpl_template
--     FROM users u JOIN clients c ON c.user_id = u.id
--    WHERE u.id = '4f2424c7-8812-4ee6-b85b-79bb73b49388';
--
--   -- tpl_template must read: invoice_salon_marketings.html
--   -- username must read:     3520223163127
--
-- If the row is missing, the user id is wrong. Find it by name instead:
--
--   SELECT u.id, u.username, c.id AS client_id, c.tpl_template
--     FROM users u JOIN clients c ON c.user_id = u.id
--    WHERE u.username ILIKE '%salon%' OR u.username = '3520223163127';
--
-- If tpl_template is right but the sheet has not changed, it is one of:
--   * Flask was not restarted after the new template was deployed
--   * you are looking at an invoice submitted BEFORE the change. Its PDF was
--     rendered and stored at submission; only a NEW invoice uses the new
--     template.
--
-- Then the buyer:
--
--   SELECT b.business_name, b.buyer_code, b.phone, b.address, b.ntn_cnic,
--          b.province, b.is_default
--     FROM buyers b JOIN clients c ON c.id = b.client_id
--    WHERE c.user_id = '4f2424c7-8812-4ee6-b85b-79bb73b49388';
--   -- expect: ABC / Walk-in Customer | B-000n | 123- | ABC | 9999997 | Punjab | t
--
--   SELECT c.sales_persons FROM clients c
--    WHERE c.user_id = '4f2424c7-8812-4ee6-b85b-79bb73b49388';
--   -- expect: []  -- it fills itself as names are typed on the invoice form
--
-- Optional: seed the list instead of waiting for the first invoice with each.
--
--   UPDATE clients SET sales_persons = '["MUBEEN"]'::jsonb
--    WHERE user_id = '4f2424c7-8812-4ee6-b85b-79bb73b49388';
-- ============================================================
