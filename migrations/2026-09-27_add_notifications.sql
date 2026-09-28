-- Migration: in-app notifications (the bell in the top bar)
-- Date: 2026-09-27
--
-- notifications       one row per message. client_id NULL = sent to every
--                     client (a broadcast); otherwise to that client only.
-- notification_reads  which client has read which notification, so the red
--                     dot on the bell clears per client -- a broadcast read by
--                     one client stays unread for the others.
--
-- Nothing in the app writes to `notifications`; you insert rows yourself.
-- Examples (run in psql or any SQL client):
--
--   -- To every client:
--   INSERT INTO notifications (title, message)
--   VALUES ('Scheduled maintenance', 'FBR submission will be unavailable Sunday 2-4 AM.');
--
--   -- To one client, with a link and an expiry:
--   INSERT INTO notifications (client_id, title, message, link, level, expires_at)
--   VALUES (42, 'Invoice template ready', 'Your letterhead template is live.',
--           '/settings/templates', 'success', NOW() + INTERVAL '14 days');
--
-- level is one of: info | success | warning | critical (only changes the icon).
-- Find a client's id with:  SELECT c.id, u.name FROM clients c JOIN users u ON u.id = c.user_id;

CREATE TABLE IF NOT EXISTS notifications (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NULL,
    title       VARCHAR(200) NOT NULL,
    message     TEXT,
    link        VARCHAR(500),
    level       VARCHAR(16) NOT NULL DEFAULT 'info',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_client_created
    ON notifications (client_id, created_at DESC);

CREATE TABLE IF NOT EXISTS notification_reads (
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    client_id       INTEGER NOT NULL,
    read_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (notification_id, client_id)
);

COMMENT ON TABLE notifications IS
  'Top-bar notifications. client_id NULL = all clients. Rows are inserted manually.';
COMMENT ON COLUMN notifications.level IS 'info | success | warning | critical';
