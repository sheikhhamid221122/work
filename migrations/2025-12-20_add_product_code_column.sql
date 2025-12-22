-- Adds product_code column for special workflows (H075895, F667833, infinityeng) without impacting other clients
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'products'
          AND column_name = 'product_code'
    ) THEN
        ALTER TABLE products
            ADD COLUMN product_code TEXT;
    END IF;
END $$;
