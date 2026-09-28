-- Migration: give every existing buyer and product a code
-- Date: 2026-09-28
--
-- New buyers get B-0001, B-0002, ... and new products P-0001, ... per client
-- (master_data.py). This numbers the records that already exist without a
-- code, in the order they were created, continuing after the highest code the
-- client already has -- so a client who typed their own B-0007 keeps it and
-- the backfill starts at B-0008.
--
-- SAFETY:
--   * Only blank codes are filled. A code someone typed is never changed.
--   * Products of the three special-username clients (H075895, F667833,
--     infinityeng) are left alone: their product codes are their own and
--     print on their invoices.
--   * Re-running it is a no-op: after the first run nothing is blank.
--   * Buyer codes are not printed on invoices or sent to FBR.
--
-- Needs products.product_code, added by 2025-12-20_add_product_code_column.sql
-- (applied before this file, as the runner goes in date order).

WITH base AS (
    SELECT client_id,
           COALESCE(MAX(CAST(SUBSTRING(UPPER(BTRIM(buyer_code)) FROM '^B-([0-9]{1,9})$') AS BIGINT)), 0) AS last_no
    FROM buyers
    GROUP BY client_id
), todo AS (
    SELECT id, client_id, ROW_NUMBER() OVER (PARTITION BY client_id ORDER BY id) AS n
    FROM buyers
    WHERE buyer_code IS NULL OR BTRIM(buyer_code) = ''
)
UPDATE buyers AS b
SET buyer_code = 'B-' || LPAD((base.last_no + todo.n)::text,
                              GREATEST(4, LENGTH((base.last_no + todo.n)::text)), '0')
FROM todo
JOIN base ON base.client_id = todo.client_id
WHERE b.id = todo.id
  AND (b.buyer_code IS NULL OR BTRIM(b.buyer_code) = '');

WITH special AS (
    SELECT c.id
    FROM clients c
    JOIN users u ON u.id = c.user_id
    WHERE BTRIM(u.username) IN ('H075895', 'F667833', 'infinityeng')
), base AS (
    SELECT client_id,
           COALESCE(MAX(CAST(SUBSTRING(UPPER(BTRIM(product_code)) FROM '^P-([0-9]{1,9})$') AS BIGINT)), 0) AS last_no
    FROM products
    GROUP BY client_id
), todo AS (
    SELECT id, client_id, ROW_NUMBER() OVER (PARTITION BY client_id ORDER BY id) AS n
    FROM products
    WHERE (product_code IS NULL OR BTRIM(product_code) = '')
      AND client_id NOT IN (SELECT id FROM special)
)
UPDATE products AS p
SET product_code = 'P-' || LPAD((base.last_no + todo.n)::text,
                                GREATEST(4, LENGTH((base.last_no + todo.n)::text)), '0')
FROM todo
JOIN base ON base.client_id = todo.client_id
WHERE p.id = todo.id
  AND (p.product_code IS NULL OR BTRIM(p.product_code) = '');
