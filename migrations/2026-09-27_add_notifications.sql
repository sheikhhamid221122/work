-- Migration: in-app notifications (the bell in the top bar)
-- Date: 2026-09-27
--
-- notifications       one row per message. client_id NULL = sent to every
--                     client (a broadcast); otherwise to that client only.
-- notification_reads  which client has read which notification, so the red
--                     dot on the bell clears per client -- a broadcast read by
--                     one client stays unread for the others.
--
-- client_id takes the same type as clients.id (a number or a UUID, whichever
-- this database uses), so the app can compare it with the logged-in client.
--
-- Nothing in the app writes to `notifications`; you insert rows yourself.
-- Examples (run in psql or any SQL client):
--
--   -- To every client:
--   INSERT INTO notifications (title, message)
--   VALUES ('Scheduled maintenance', 'FBR submission will be unavailable Sunday 2-4 AM.');
--
--   -- To one client, with a link and an expiry (use that client's clients.id):
--   INSERT INTO notifications (client_id, title, message, link, level, expires_at)
--   SELECT c.id, 'Invoice template ready', 'Your letterhead template is live.',
--          '/settings/templates', 'success', NOW() + INTERVAL '14 days'
--   FROM clients c JOIN users u ON u.id = c.user_id
--   WHERE u.username = '<their login username>';
--
-- level is one of: info | success | warning | critical (only changes the icon).
-- Find a client's id with:  SELECT c.id, u.username, u.name FROM clients c JOIN users u ON u.id = c.user_id;

DO $$
DECLARE
    id_type text;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod) INTO id_type
    FROM pg_attribute a
    WHERE a.attrelid = 'clients'::regclass AND a.attname = 'id' AND NOT a.attisdropped;

    EXECUTE format($sql$
        CREATE TABLE IF NOT EXISTS notifications (
            id          SERIAL PRIMARY KEY,
            client_id   %s NULL,
            title       VARCHAR(200) NOT NULL,
            message     TEXT,
            link        VARCHAR(500),
            level       VARCHAR(16) NOT NULL DEFAULT 'info',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ NULL
        )$sql$, COALESCE(id_type, 'integer'));

    EXECUTE format($sql$
        CREATE TABLE IF NOT EXISTS notification_reads (
            notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
            client_id       %s NOT NULL,
            read_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (notification_id, client_id)
        )$sql$, COALESCE(id_type, 'integer'));
END $$;

CREATE INDEX IF NOT EXISTS idx_notifications_client_created
    ON notifications (client_id, created_at DESC);

COMMENT ON TABLE notifications IS
  'Top-bar notifications. client_id NULL = all clients. Rows are inserted manually.';
COMMENT ON COLUMN notifications.level IS 'info | success | warning | critical';
