-- Migration: Add per-account billing suspension flag
-- Date: 2026-08-10
-- Purpose: Allow locking a single account out of the app when its annual retainer
--          is outstanding, without deleting any data. Enforced in app.py by the
--          /login handler and the global before_request guard.
--
-- NOTE: These columns were already applied directly to production via pgAdmin on
--       2026-08-10. This file records the change so the schema history is complete;
--       it is idempotent and re-running it is a no-op.

ALTER TABLE users ADD COLUMN IF NOT EXISTS access_suspended BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS suspension_reason TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;

COMMENT ON COLUMN users.access_suspended IS 'When true, the account is blocked from logging in and from every authenticated route (HTTP 402). No data is deleted; set back to FALSE to restore access without a redeploy.';
COMMENT ON COLUMN users.suspension_reason IS 'Internal admin note explaining why the account was suspended. Not displayed to the user anywhere in the app.';
COMMENT ON COLUMN users.suspended_at IS 'Timestamp the account was most recently suspended.';

-- To suspend an account:
--   UPDATE users SET access_suspended = TRUE, suspension_reason = '...', suspended_at = NOW() WHERE id = '<uuid>';
-- To restore an account (takes effect within ~30s, no redeploy or restart required):
--   UPDATE users SET access_suspended = FALSE, suspended_at = NULL WHERE id = '<uuid>';
