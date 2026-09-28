-- Migration: give a builder-made template a name
-- Date: 2026-09-18
--
-- The template designer has a name field in its toolbar, so the template a
-- client designs can be identified in the Templates gallery instead of
-- showing as a bare "Custom". Purely a label: nothing renders from it.
--
-- SAFETY: nullable with no default. NULL means "unnamed", and the designer
-- falls back to "My Custom Template" for display only. Existing clients are
-- unaffected and no invoice output changes.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_custom_name VARCHAR(60);

COMMENT ON COLUMN clients.tpl_custom_name IS
  'Display name for the client''s designer-made template; NULL = unnamed';
