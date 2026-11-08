BEGIN;
SET LOCAL lock_timeout = '10s';
SELECT pg_advisory_xact_lock(710020);
CREATE TABLE IF NOT EXISTS market.telegram_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    initialized_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS market.telegram_batches (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('sending', 'sent', 'failed', 'unknown')),
    message_text text NOT NULL,
    chat_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    telegram_message_id bigint,
    attempts integer NOT NULL DEFAULT 1,
    retry_at timestamptz,
    error_code text
);
CREATE TABLE IF NOT EXISTS market.telegram_event_log (
    event_id bigint PRIMARY KEY REFERENCES market.events(id),
    batch_id bigint REFERENCES market.telegram_batches(id),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
-- NULL batch_id marks events existing before notifications were enabled.
-- The initialization marker makes rerunning this migration safe.
LOCK TABLE market.events IN SHARE MODE;
INSERT INTO market.telegram_event_log (event_id)
SELECT id FROM market.events
WHERE NOT EXISTS (SELECT 1 FROM market.telegram_settings)
ON CONFLICT DO NOTHING;
INSERT INTO market.telegram_settings (singleton) VALUES (true)
ON CONFLICT DO NOTHING;
COMMIT;
SELECT count(*) AS historical_events_skipped
FROM market.telegram_event_log WHERE batch_id IS NULL;
