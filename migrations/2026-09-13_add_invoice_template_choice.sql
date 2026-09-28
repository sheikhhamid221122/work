-- Migration: let a client choose their own invoice template
-- Date: 2026-09-13
--
-- Until now the invoice template was chosen by a hardcoded `if username == ...`
-- chain in app.py, so onboarding a client meant a code change. These columns
-- make it a setting the client owns.
--
-- SAFETY: every column is nullable with no default, and NULL means "behave
-- exactly as before". A client who never opens the new Templates screen keeps
-- the template the username chain already gave them. Nothing here changes an
-- existing client's output.

-- Which layout. NULL = fall through to the legacy per-username mapping.
-- Values: one of invoice_templates.LAYOUTS ids ('classic', 'modern', ...),
-- 'custom' for a template built in the template builder, or a legacy
-- 'invoice_*.html' filename.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_template VARCHAR(40);

-- Presentation. All nullable; resolve_theme() supplies validated defaults.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_accent_color VARCHAR(7);
-- Hex including '#', e.g. '#1e3a8a'. Invalid values fall back to navy.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_font VARCHAR(16);
-- 'sans' | 'serif' | 'condensed' | 'mono'

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_density VARCHAR(16);
-- 'compact' | 'comfortable' | 'spacious'

-- Letterhead.
--
-- Two different clients are served here:
--   * PRE-PRINTED paper: enable letterhead, set the millimetres their printed
--     masthead occupies, upload NOTHING. The invoice leaves that space blank.
--   * DIGITAL letterhead: enable it, upload the image, and it is printed into
--     the reserved space.
-- Either way the space is reserved on page 1 only -- a long invoice does not
-- waste 120mm at the top of every sheet.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_letterhead_enabled BOOLEAN DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_letterhead_mm INTEGER DEFAULT 120;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_letterhead_url TEXT;

-- Logo and QR sizing, in millimetres on the printed page.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_logo_mm NUMERIC(5,1);
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_qr_mm NUMERIC(5,1);

-- A template built in the template builder, stored as a block spec.
-- Never raw HTML: the spec is validated against a known block vocabulary
-- before rendering, so a stored template cannot inject markup into the PDF
-- and cannot drop the FBR compliance block.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_custom_spec JSONB;

COMMENT ON COLUMN clients.tpl_template IS
  'Invoice layout id; NULL = legacy per-username mapping in app.py';
COMMENT ON COLUMN clients.tpl_letterhead_mm IS
  'Millimetres reserved at the top of page 1 for a letterhead (default 120)';
COMMENT ON COLUMN clients.tpl_custom_spec IS
  'Block spec for a builder-made template; validated, never raw HTML';
